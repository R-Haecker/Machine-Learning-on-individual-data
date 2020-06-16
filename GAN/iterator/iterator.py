import os
import numpy as np
import random

import torch
import torch.nn as nn

from edflow import TemplateIterator, get_logger
from edflow.hooks.checkpoint_hooks.common import get_latest_checkpoint
from edflow.util import walk

from iterator.util import (
    get_loss_funct,
    np2pt,
    pt2np,
    weights_init
) 

class Iterator(TemplateIterator):
    def __init__(self, config, root, model, *args, **kwargs):
        super().__init__(config, root, model, *args, **kwargs)
        # export to the right gpu if specified in the config
        self.set_gpu()
        # get the config and the logger
        self.config = config
        self.set_random_state()
        # Config will be tested inside the Model class even for the iterator
        self.logger = get_logger("Iterator")
        # Log the architecture of the model
        self.logger.debug(f"{model}")
        netD = model.discriminator
        netD.to(self.device)
        self.netD = netD.apply(weights_init)
        amp = self.config["optimization"]["factor_disc_lr"] if "factor_disc_lr" in self.config["optimization"] else 1
        self.optimizerD = torch.optim.Adam(self.netD.parameters(), lr=self.config["learning_rate"] * amp, betas=(self.config["beta1"], 0.999) )  #weight_decay=self.config["weight_decay"])

        netG = model.generator
        self.netG = netG.to(self.device)
        # can not init weights with VUNet module: NormConv2d
        # self.netG.enc = netG.enc.apply(weights_init)
        self.optimizerG = torch.optim.Adam(self.netG.parameters(), lr=self.config["learning_rate"], betas=(self.config["beta1"], 0.999) )

        self.real_label = torch.ones(self.config["batch_size"], device=self.device)
        self.fake_label = torch.zeros(self.config["batch_size"], device=self.device)
        if "variational" in self.config:
            # get the offset for the sigmoid regulator for the variational part of the loss 
            self.x_offset_KLD = self.offset_KLD_weight()
    
    def set_random_state(self):
        np.random.seed(self.config["random_seed"])
        torch.random.manual_seed(self.config["random_seed"])

    def prepare_logs(self, losses, input_images, G_output_images):
        """Return a log dictionary with all instersting data to log."""
        def conditional_convert2np(log_item):
            if isinstance(log_item, torch.Tensor):
                log_item = log_item.detach().cpu().numpy()
            return log_item
        # create a dictionary to log with all interesting variables 
        logs = {
            "images": {},
            "scalars":{
                **losses
                }
        }
        # log the input and output images
        in_img = pt2np(input_images)
        out_img = pt2np(G_output_images)
        # log the images as batches
        logs["images"]["input_batch"] = in_img
        logs["images"]["output_batch"] = out_img
        # log the images separately
        for i in range(self.config["batch_size"]):
            logs["images"].update({"input_" + str(i): np.expand_dims(in_img[i],0)})
            logs["images"].update({"output_" + str(i): np.expand_dims(out_img[i],0)})
        # convert to numpy
        walk(logs, conditional_convert2np, inplace=True)
        return logs

    def criterion(self, D_output_real, D_output_fake, G_D_output_fake, input_images, G_output_images, D_output_fake_sampled = None, G_D_output_fake_sampled = None):
        """This function returns a dictionary with all neccesary losses for the model."""
        # convert to numpy
        real   = np.mean(pt2np(D_output_real, False))
        fake   = np.mean(pt2np(D_output_fake, permute=False))
        fake2 = np.mean(pt2np(G_D_output_fake, permute=False))
        
        losses = {}
        losses["D_outputs"] = {}
        losses["D_outputs"]["real"]  = real
        losses["D_outputs"]["fake"]  = fake
        if D_output_fake_sampled != None:
            losses["D_outputs"]["fake_sampled"]  = np.mean(pt2np(D_output_fake_sampled, False))
        ###################
        ## Discriminator ##
        ###################
        self.netD.zero_grad()
        disc_loss = get_loss_funct(self.config["losses"]["discriminator_loss"])
        # real images
        D_loss_real = disc_loss(D_output_real, self.real_label)
        # fake images
        D_loss_fake = disc_loss(D_output_fake, self.fake_label)
        # sampled fake images
        if D_output_fake_sampled != None:
            D_loss_fake_sampled = disc_loss(D_output_fake_sampled, self.fake_label)
            # total D_loss
            D_loss = D_loss_fake + D_loss_real + D_loss_fake_sampled
        else:
            # total D_loss
            D_loss = D_loss_fake + D_loss_real
        
        ###################
        #### Generator ####
        ###################
        self.netG.zero_grad()
        G_D_loss_recon = disc_loss(G_D_output_fake, self.real_label)
        if G_D_output_fake_sampled != None:
            G_D_loss_sampled = disc_loss(G_D_output_fake_sampled, self.real_label) 
            G_D_loss = G_D_loss_recon + G_D_loss_sampled
        else:
            G_D_loss = G_D_loss_recon
        
        # save losses
        D_loss_       = torch.mean(D_loss)
        D_loss_fake_  = np.mean(pt2np(D_loss_fake, False))
        D_loss_real_  = np.mean(pt2np(D_loss_real, False))
        losses["D_loss"] = D_loss_
        losses["D_loss_fake"] = D_loss_fake_
        losses["D_loss_real"] = D_loss_real_
        
        G_D_loss_       = torch.mean(G_D_loss)
        G_D_loss_recon_ = torch.mean(G_D_loss_recon)
        losses["G_D_loss"] = G_D_loss_
        losses["G_D_loss_recon"] = G_D_loss_recon_
        
        if "optimization" in self.config and "latent_sample" in self.config["optimization"] and self.config["optimization"]["latent_sample"]:
            losses["D_loss_fake_sampled"] = np.mean(pt2np(D_loss_fake_sampled, permute=False))
            losses["G_D_loss_sampled"] = np.mean(pt2np(G_D_loss_sampled, permute=False))

        # only use if the vae model is used
        if "conv" in self.config:
            # calculate all losses according to reconstruction
            losses["reconstruction_loss"] = self.reconstruction_losses(recon_input = input_images, reconstructed_image = G_output_images)
            losses["G_loss"] = G_D_loss_ + losses["reconstruction_loss"]["total_loss"]
        else:
            losses["G_loss"] = G_D_loss_
        return losses        

    def step_op(self, model, **kwargs):
        '''This function will be called every step in the training.'''
        # get inputs
        input_images = kwargs["image"]
        input_images = torch.tensor(input_images).to(self.device)
        ## Generator ##
            # genarate fake images
        if "conv" in self.config:
            G_output_images = self.netG(input_images)
            # evaluate generated images
        G_D_output_fake = self.netD(G_output_images).view(-1)
        
        ## Discriminator ##
            # input images
        self.logger.debug("input_images.shape: " + str(input_images.shape))
        D_output_real = self.netD(input_images).view(-1)
        self.logger.debug("output.shape: " + str(D_output_real.shape))
            # generated image
        D_output_fake = self.netD(G_output_images.detach()).view(-1)
        
        if "optimization" in self.config and "latent_sample" in self.config["optimization"] and self.config["optimization"]["latent_sample"]:
            mu_ = torch.rand([self.config["batch_size"], self.netG.latent_dim], device = self.device)
            G_output_sampled_images = self.netG.latent_sample(mu = mu_)
            G_D_output_fake_sampled = self.netD(G_output_sampled_images).view(-1)
            D_output_fake_sampled = self.netD(G_output_sampled_images.detach()).view(-1)
        else:
            D_output_fake_sampled = None
            G_D_output_fake_sampled = None

        # create all losses
        losses = self.criterion(D_output_real = D_output_real, D_output_fake = D_output_fake, G_D_output_fake = G_D_output_fake, input_images = input_images, 
                                G_output_images = G_output_images, D_output_fake_sampled = D_output_fake_sampled, G_D_output_fake_sampled = G_D_output_fake_sampled)
            
        def train_op():
            # This function will be executed if the model is in training mode
            if self.config["optimization"]["update"] == "one":
                G , D = self.get_comparable_losses(losses)
                if G > D:
                # choose if generator or discriminator will be updated
                    losses["G_loss"].backward()
                    self.optimizerG.step()
                    losses["Update_Generator"] = 1
                    losses["Update_Discriminator"] = 0
                else:
                    losses["D_loss"].backward()
                    self.optimizerD.step()
                    losses["Update_Generator"] = 0
                    losses["Update_Discriminator"] = 1
            elif self.config["optimization"]["update"] == "one_prob":
                if self.choose_G_loss(*self.get_comparable_losses(losses)):
                # choose if generator or discriminator will be updated
                    losses["G_loss"].backward()
                    self.optimizerG.step()
                    losses["Update_Generator"] = 1
                    losses["Update_Discriminator"] = 0
                else:
                    losses["D_loss"].backward()
                    self.optimizerD.step()
                    losses["Update_Generator"] = 0
                    losses["Update_Discriminator"] = 1
            elif self.config["optimization"]["update"] == "both":
                # update both generator and discriminator will be updated
                losses["G_loss"].backward()
                self.optimizerG.step()
                losses["D_loss"].backward()
                self.optimizerD.step()
                losses["Update_Generator"] = 1
                losses["Update_Discriminator"] = 1
                
        def log_op():
            logs = self.prepare_logs(losses, input_images = input_images, G_output_images = G_output_images)
            return logs

        def eval_op():
            # This function will be executed if the model is in evaluation mode
            z = pt2np(self.netG.z, permute = False)
            return {"labels": {"latent_rep": z }}

        return {"train_op": train_op, "log_op": log_op, "eval_op": eval_op}
    
    def get_comparable_losses(self, losses):
        if "optimization" in self.config and "latent_sample" in self.config["optimization"] and self.config["optimization"]["latent_sample"]:
            compare_D_loss = losses["D_loss"]/3
            compare_G_loss = losses["G_D_loss"]/2
        else:
            compare_D_loss = losses["D_loss"]/2
            compare_G_loss = losses["G_D_loss"]
        return [compare_G_loss, compare_D_loss]        

    def save(self, checkpoint_path):
        '''Save the weights of the model to the checkpoint_path.'''
        # TODO check if model.state works with disc and gen
        state = {
            "model": self.model.state_dict(),
            "optimizerD": self.optimizerD.state_dict(),
            "optimizerG": self.optimizerG.state_dict()
        }
        torch.save(state, checkpoint_path)

    def restore(self, checkpoint_path):
        '''Load the weigths of the model from a previous training.'''
        state = torch.load(checkpoint_path)
        self.model.load_state_dict(state["model"])
        self.optimizer.load_state_dict(state["optimizerD"])
        self.optimizer.load_state_dict(state["optimizerG"])
    
    def set_gpu(self):
        """Move the model to device cuda if available and use the specified GPU"""
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.logger.debug(f"Model will pushed to the device: {self.device}")
        if "CUDA_VISIBLE_DEVICES" in self.config:
            if type(self.config["CUDA_VISIBLE_DEVICES"]) != str:
                self.config["CUDA_VISIBLE_DEVICES"] = str(self.config["CUDA_VISIBLE_DEVICES"])
            os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
            os.environ["CUDA_VISIBLE_DEVICES"] = self.config["CUDA_VISIBLE_DEVICES"]

    def choose_G_loss(self, G_loss, D_loss, a = 0.05, b = 0.7, c = 1.4):
        with torch.no_grad():
            delta = pt2np( G_loss - D_loss, permute = False)
            if delta < 0:
                G_higher = False
                delta = np.abs(delta)
            else:
                G_higher = True 
            sigma = delta/c + np.maximum(0,(-delta+b)/(b))*a
            
            delta = np2pt(delta, permute = False)
            sigma = np2pt(sigma, permute = False)

            norm_dist = torch.distributions.normal.Normal(delta, sigma)
            res = norm_dist.sample()
            #norm = np.random.normal(delta, sigma)
            return not G_higher if res < 0 else G_higher

    def reconstruction_losses(self, recon_input, reconstructed_image):
        # get the reconstruction loss
        reconstruction_loss = get_loss_funct(self.config["losses"]["reconstruction_loss"])
        # compute the reconstruction loss
        recon_loss = reconstruction_loss(reconstructed_image, recon_input)
        mean_recon_loss = torch.mean(recon_loss)
        # reconstruction loss is to small trying it with ten times of it
        mean_recon_loss = mean_recon_loss * self.config["losses"]["reconstruction_loss_weight"]
        min_recon_loss  = np.min( pt2np(recon_loss, permute = False))
        max_recon_loss  = np.max( pt2np(recon_loss, permute = False))
        recon_losses = {}
        recon_losses[self.config["losses"]["reconstruction_loss"]] = {}
        recon_losses[self.config["losses"]["reconstruction_loss"]]["min"] = min_recon_loss
        recon_losses[self.config["losses"]["reconstruction_loss"]]["max"] = max_recon_loss
        recon_losses[self.config["losses"]["reconstruction_loss"]]["mean"] = mean_recon_loss
        # track and only view an additional loss if specified
        if "view_loss" in self.config["losses"]:
            view_criterion = get_loss_funct(self.config["losses"]["view_loss"])
            # calculate a secound reconstruction loss to log if wanted 
            view_loss = view_criterion(reconstructed_image, recon_input)
            view_mean_loss = pt2np(torch.mean(view_loss), permute = False)
            view_min_loss  = np.min(pt2np(view_loss, permute = False))
            view_max_loss  = np.max(pt2np(view_loss, permute = False))   
            recon_losses["view_" + self.config["losses"]["view_loss"]] = view_min_loss
            recon_losses["view_" + self.config["losses"]["view_loss"]]["max"] = view_max_loss
            recon_losses["view_" + self.config["losses"]["view_loss"]]["mean"] = view_mean_loss
        # compute the loss for the variational part
        assert "variational" in self.config, "variational should be in config"
        # Calculate loss for latent representation if the model is a VAE
        step = torch.tensor(self.get_global_step(), dtype = torch.float)
        KLD_weight = self.get_KLD_weight(step).to(self.device)
        recon_losses["KLD_loss"] = {}
        if KLD_weight != 0:
            if "sigma" in self.config["variational"] and self.config["variational"]["sigma"]:
                # 0.5 * sum(1 + log(sigma^2) - mu^2 - sigma^2)
                sig_ = self.netG.var
                sigma_sq = self.netG.var.pow(2)
                mu_ = self.netG.mu
                mu_sq = self.netG.mu.pow(2)
                sigma_sq_log = torch.log(sigma_sq)
                #KLD_loss = -0.5 * torch.sum(1 + self.netG.var.pow(2).log() - self.netG.mu.pow(2) - self.netG.var.pow(2))
                KLD_loss = -0.5 * torch.sum(1 + sigma_sq_log - mu_sq - sigma_sq)
                KLD_loss_weighted = KLD_weight * KLD_loss 
                recon_losses["KLD_loss"]["mean"] = KLD_loss
                '''print("sig_.shape:",sig_.shape)
                print("sig_",sig_)
                print("")
                print("sig_sq",sigma_sq)
                print("")
                print("sig_sq_log", sigma_sq_log)
                print("")
                print("")
                print("mu_.shape:",mu_.shape)
                print("mu_",mu_)
                print("")
                print("mu_sq",mu_sq)
                print("")
                print("")
                print("KLD_loss:",KLD_loss)
                print("")
                print("KLD_loss_weighted",KLD_loss_weighted)
                '''#assert 1==0
            else:
                abs_z = torch.mean(torch.abs(self.netG.z))
                # force absolute value of latent representation (with a regulation factor) into the loss to minimize it
                KLD_loss_weighted = KLD_weight * abs_z
                recon_losses["KLD_loss"]["mean"] = abs_z
        else:
            KLD_weight = 0
            KLD_loss_weighted = 0
        recon_losses["KLD_loss"]["weight"] = KLD_weight
        recon_losses["KLD_loss"]["mean_weighted"] = KLD_loss_weighted 
        total_recon_loss = mean_recon_loss + KLD_loss_weighted
        recon_losses["total_loss"] = total_recon_loss

        return recon_losses
        
    def offset_KLD_weight(self):
        '''Return the offset of the sigmoid function for the loss on mu given the parameter from the config.'''
        if "KLD_loss" in self.config["losses"] and "start_step" in self.config["losses"]["KLD_loss"] and "width" in self.config["losses"]["KLD_loss"] and "amplitude" in self.config["losses"]["KLD_loss"]:
            if "start_amplitude" not in self.config["losses"]["KLD_loss"]:
                self.config["losses"]["KLD_loss"]["start_amplitude"] = 1.E-5
            # check for all parameter
            x_offset_KLD = self.config["losses"]["KLD_loss"]["start_step"] - self.config["losses"]["KLD_loss"]["width"]/3 * (np.tan((2*self.config["losses"]["KLD_loss"]["start_amplitude"])/self.config["losses"]["KLD_loss"]["amplitude"] - 1))
        else:
            self.logger.info("parameters for sigmoid regulator are not specified now choosing default.")
            self.config["losses"]["KLD_loss"]["start_step"] = 50000
            self.config["losses"]["KLD_loss"]["start_amplitude"] = 10**(-5) 
            self.config["losses"]["KLD_loss"]["width"] = 2000
            self.config["losses"]["KLD_loss"]["amplitude"] = 0.001
            x_offset_KLD = self.offset_KLD_weight()
        return x_offset_KLD

    def get_KLD_weight(self, step):
        '''Return the sigmoid regulator for the absolute loss of the varaitional expected value mu.'''
        if step < self.config["losses"]["KLD_loss"]["start_step"]:
            return torch.Tensor([0])
        else:
            return self.config["losses"]["KLD_loss"]["amplitude"] * (0.5 + 0.5 * np.tanh((step - self.x_offset_KLD)*3/self.config["losses"]["KLD_loss"]["width"]))
