import numpy as np
import torch
import torch.nn as nn

def get_tensor_shapes(config, encoder = True):
    """This function calculates the shape of a every tensor after an operation in the VAE_Model.        
    :return: A list of the shape of an tensors after every module.
    :rtype: List
    """        
    tensor_shapes = []
    # The first shape is specified in the config
    tensor_shapes.append([3, config["image_resolution"][0],config["image_resolution"][1]])
    # calculate the shape after a convolutuonal operation
    '''
    if ([ config["conv"]["kernel_size"], config["conv"]["stride"] ] in [[4,3],[6,3]] or ("upsample" in config and [ config["conv"]["kernel_size"], config["conv"]["stride"] ] in [[2,2],[3,3],[4,4]])):
        # these cases just have one less spacial dimension in the last iteration...  
        for i in range(config["conv"]["n_blocks"]): 
            # calculate the spacial resolution
            spacial_res = (int((tensor_shapes[i][-1] + 2 * config["conv"]["padding"] - config["conv"]["kernel_size"] - 2)
                        /config["conv"]["stride"] + 1) + 1)
            if "upsample" in config:
                spacial_res = int(np.floor(spacial_res/2) +1) 
            if i == config["conv"]["n_blocks"]-1:
                spacial_res = int(spacial_res - 1 )
            tensor_shapes.append([config["conv"]["conv_channels"][i+1], spacial_res, spacial_res])    
    else:
    '''    
    if "first_layer" in config["conv"] and config["conv"]["first_layer"]:
        tensor_shapes.append([config["conv"]["conv_channels"][1], config["image_resolution"][0], config["image_resolution"][1] ])
        range_ = [1, config["conv"]["n_blocks"] + 1 ]
    else:
        range_ = [0, config["conv"]["n_blocks"]]
    # normal formular to compute the tensor shapes     
    for i in range(*range_):
        # calculate the spacial resolution after the formular given from pytorch
        spacial_res = (int((tensor_shapes[i][-1] + 2 * config["conv"]["padding"] - config["conv"]["kernel_size"] - 2)
                    /config["conv"]["stride"] + 1) + 1)
        if "upsample" in config:
            spacial_res = int(np.floor(spacial_res/2) +1) 
        if encoder and i == range_[1] - 1 and not "linear" in config and "variational" in config and "sigma" in config["variational"] and config["variational"]["sigma"]:
            tensor_shapes.append([config["conv"]["conv_channels"][i+1] * 2, spacial_res, spacial_res])
        else:
            tensor_shapes.append([config["conv"]["conv_channels"][i+1], spacial_res, spacial_res])
            
    # add the shape of the flatten image if a fc linaer layer is available
    if "variational" in config or "linear" in config:
        flatten_rep = tensor_shapes[range_[1]][1] * tensor_shapes[range_[1]][2] * tensor_shapes[range_[1]][0] #config["conv"]["conv_channels"][config["conv"]["n_blocks"]]
        tensor_shapes.append([flatten_rep])
        if "linear" in config:
            if encoder and "variational" in config and "sigma" in config["variational"] and config["variational"]["sigma"]:
                tensor_shapes.append([config["linear"]["latent_dim"] * 2 ])
            else:
                tensor_shapes.append([config["linear"]["latent_dim"]])

    return tensor_shapes

def set_random_state(config):
    np.random.seed(config["random_seed"])
    torch.random.manual_seed(config["random_seed"])

def test_config(config):
    ''' Test the config if it will work with the VAE_Model.'''
    assert "shuffle_dataset" in config
    #need to allow it for phi seq dataset  assert config["shuffle_dataset"] == False, "shuffel data set is not allowed anymore." 
    #we log indices now assert "request_parameters" in config and config["request_parameters"] == True, "We want to log parameters from now on."
    assert "activation_function" in config, "For this model you need to specify the activation function: possible options :{'ReLU, LeakyReLu, Sigmoid, LogSigmoid, Tanh, SoftMax'}"
    assert "image_resolution" in config, "You have to specify the resolution of the images which are given to the model."
    assert "conv" in config, "You have to use convolutional operations specified in config['conv']"
    if "n_channel_start" not in config["conv"] or "n_channel_max" not in config["conv"]:
        assert "kernel_size" in config["conv"], "For this convolutional model you have to specify the kernel size of all convolutions."
        assert "stride" in config["conv"], "For this convolutional model you have to specify the stride value of all convolutions."    
    assert ("conv_channels" in config["conv"]) or all(key in config["conv"] for key in ("n_channel_start", "n_channel_max")), "The amount of channels at every convolution have to be specified in the config nested in 'conv' at 'conv_channels'."
    if "n_blocks" in config["conv"]:
        assert "conv_channels" in config["conv"] # maybe write something
    #if all(key in config["conv"] for key in ("conv_channels", "n_blocks")):
    #    assert len(config["conv"]["conv_channels"]) == config["conv"]["n_blocks"]+1, "The first conv_chanel is three for RGB --> The amount of convolutional blocks: 'n_blocks' = " + str(config["conv"]["n_blocks"]) + " plus one has to be the same as the length of the 'conv_channels' list: len('conv_channels') = " + str(len(config["conv"]["conv_channels"])) 
    if "linear" in config:
        assert "latent_dim" in config["linear"] and config["linear"]["latent_dim"] > 0, "If linear is in config laten dim has to be greater than zero."
    # Test config for iterator parameters
    assert "losses" in config, "You have to specify the losses used in the model in config['losses']"
    assert "reconstruction_loss" in config["losses"], "The config must contain and define a Loss function for image reconstruction. possibilities:{'L1','L2'or'MSE'}."
    assert "learning_rate" in config, "The config must contain and define a the learning rate."
    if config["model"] == "model.gan.GAN":
        assert "discriminator_loss" in config["losses"], "if you are training a GAN you need to specify a discriminator loss e.g. 'BCE' or 'L2'"
        assert "optimization" in config , "If you are training a GAN you need to specify: config['optimization']['update'] with: 'one', 'one_prob', 'both'. No config['optimization'] found!"
        assert "update" in config["optimization"] , "If you are training a GAN you need to specify: config['optimization']['update'] with: 'one', 'one_prob', 'both'. No config['optimization']['update'] found!"
        assert config["optimization"]["update"] in ["one", "one_prob", "both", "accuracy"], "You have to choose from the given options."
        if config["optimization"]["update"] == "accuracy":
            assert "accuracy_threshold" in config["optimization"]
        if "accuracy_threshold" in config["optimization"]:
            assert config["optimization"]["update"] == "accuracy"
        if "reduce_lr" in config["optimization"]:
            assert type(config["optimization"]["reduce_lr"]) in [int,float]
            assert "num_steps" in config, "From now on only use num_steps to set training time."
        if "metric_loss" in config["losses"]:
            assert "phi_margin" in config["losses"]["metric_loss"]
            assert "alpha_margin" in config["losses"]["metric_loss"]
            assert "weight" in config["losses"]["metric_loss"]
    
def complete_config(config, logger):
    if "metric_loss" in config["losses"] and config["losses"]["metric_loss"]:
        if "request_parameters" not in config or config["request_parameters"]==False:
            config["request_parameters"] = True
            logger.debug("config['metric_loss'] == True and config['request_parameters'] == False or not set. config['request_parameters'] has to be True and is now set to True.")
    assert "random_seed" in config, "If you use the normla data set there should be a 'random_seed' in the config."
    if type(config["image_resolution"])!=list:
        config["image_resolution"]=[config["image_resolution"], config["image_resolution"]]
    if "n_channel_start" in config["conv"] and "n_channel_max" in config["conv"]:
        if "kernel_size" not in config["conv"] or "stride" not in config["conv"]:
            config["conv"]["kernel_size"] = 3
            config["conv"]["stride"] = 2

    if "n_blocks" in config["conv"]:
        if not config["conv"]["conv_channels"][0] == 3:
            config["conv"]["conv_channels"].insert(0, 3)
            logger.info("In config the first conv chanlles should always be three. It is now added.")
    else:
        if "conv_channels" in config["conv"]:
            if not config["conv"]["conv_channels"][0] == 3:
                config["conv"]["conv_channels"].insert(0, 3)
                logger.info("In config the first conv chanlles should always be three. It is now added.")
            # n_blocks is specified with the length of the conv_channels list
            config["conv"]["n_blocks"] = len(config["conv"]["conv_channels"]) - 1
        else:
            # if both not specified: do downsampling by factor two until spacial size is one 
            assert config["conv"]["stride"] == 2, "If 'conv_channels' and 'n_blocks' is not specified in config the stride of the conv. has to be two."
            config["conv"]["n_blocks"] = int(np.round(np.log2(config["image_resolution"][0])))
            config["conv"]["conv_channels"] = [] 
            config["conv"]["conv_channels"].append(3)
            channels = int(config["conv"]["n_channel_start"])
            for i in range(config["conv"]["n_blocks"]):
                config["conv"]["conv_channels"].append(channels)
                '''
                if not("first_layer" in config["conv"] and config["conv"]["first_layer"]) and (i == (config["conv"]["n_blocks"]-2) and "n_channel_end" in config["conv"]):
                        channels = config["conv"]["n_channel_end"]
                elif ("first_layer" in config["conv"] and config["conv"]["first_layer"]) and (i == (config["conv"]["n_blocks"]-1) and "n_channel_end" in config["conv"]):
                        channels = config["conv"]["n_channel_end"]
                else:
                '''
                channels = int( np.minimum(int(channels*2),config["conv"]["n_channel_max"]) )
                
            if "first_layer" in config["conv"] and config["conv"]["first_layer"]:
                config["conv"]["conv_channels"].append(channels)
    if "padding" not in config["conv"] or "upsample" in config or config["conv"]["padding"] == None:
        if config["conv"]["kernel_size"]%2 == 0:
            config["conv"]["padding"] = 0
            # not sure if this is the best 
        else:
            config["conv"]["padding"] = config["conv"]["kernel_size"]//2
        logger.info("Padding of the convolutions is set to " + str(config["conv"]["padding"]))
    if "weight_decay" not in config:
        config["weight_decay"] = 0
    return config


def get_act_func(config, logger):
    """This function retruns the specified activation function from the config."""

    if config["activation_function"] == "ReLU":
        if "ReLU" in config:
            logger.debug("activation function: changed ReLu to leakyReLU with secified slope!")
            return nn.LeakyReLU(negative_slope=config["ReLu"])
        else:
            logger.debug("activation function: ReLu")
            return nn.ReLU(True)  
    if config["activation_function"] == "LeakyReLU":
        if "LeakyReLU_negative_slope" in config:
            logger.debug("activation_function: LeakyReLU")
            return nn.LeakyReLU(negative_slope=config["LeakyReLU_negative_slope"])
        elif "LeakyReLU" in config:
            logger.debug("activation_function: LeakyReLU")
            return nn.LeakyReLU(negative_slope=config["LeakyReLU"])
        else:
            logger.debug("activation function: LeakyReLu changed to ReLU because no slope value could be found")
            return nn.LeakyReLU()
    if config["activation_function"] == "Sigmoid":
        logger.debug("activation_function: Sigmoid")
        return nn.Sigmoid
    if config["activation_function"] == "LogSigmoid":
        logger.debug("activation_function: LogSigmoid")
        return nn.LogSigmoid
    if config["activation_function"] == "Tanh":
        logger.debug("activation_function: Tanh")
        return nn.Tanh
    if config["activation_function"] == "SoftMax":
        logger.debug("activation_function: SoftMax")
        return nn.SoftMax()