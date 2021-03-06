import torch
import numpy as np
import torch.nn as nn

def get_loss_funct(loss_function):
    '''Get the loss function specified in the config.'''
    if loss_function == "L1":
        return nn.L1Loss()
    if loss_function == "L2" or loss_function == "MSE":
        return nn.MSELoss()
    if loss_function == "BCE":
        return nn.BCELoss()

# These two function are copied from the VUNet repository: https://github.com/jhaux/VUNet.git
def np2pt(array, permute = True):
    '''Converts a numpy array to torch Tensor. If possible pushes to the
    GPU.'''
    tensor = torch.tensor(array, dtype=torch.float32)
    if torch.cuda.is_available():
        tensor = tensor.cuda()
    if permute:
        tensor = tensor.permute(0, 3, 1, 2)
    tensor = tensor.contiguous()
    return tensor

def pt2np(tensor, permute = True):
    '''Converts a torch Tensor to a numpy array.'''
    array = tensor.detach().cpu().numpy()
    if permute:
        array = np.transpose(array, (0, 2, 3, 1))
    return array

