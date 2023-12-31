#import imp
import os, sys
import torch, torchvision
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join('external_src', 'psmnet'))
sys.path.insert(0, os.path.join('external_src', 'psmnet', 'models'))
from external_src.PSMNet.models.stackhourglass import PSMNet


class PSMNetModel(object):
    '''
    Wrapper class for PSMNet model

    Arg(s):
        num_deform_layers : int
            number of deformable convolution layers [0, 6, 25]
        device : torch.device
            cpu or cuda device to run on
    '''

    def __init__(self, num_deform_layers=0, device=torch.device('cuda')):

        self.max_disparity = 192
        self.num_deform_layers = num_deform_layers
        self.device = device

        # Restore depth prediction network
        self.model = PSMNet(
            maxdisp=self.max_disparity,
            num_deform_layers=self.num_deform_layers)

        # Move to device
        self.to(self.device)
        self.eval()

    def forward(self, image0, image1, mode_type='CV'):
        '''
        Forwards stereo pair through the network

        Args:
            image0 : torch.Tensor[float32]
                N x C x H x W left image
            image1 : torch.Tensor[float32]
                N x C x H x W right image
        Returns:
            torch.Tensor[float32] : N x 1 x H x W disparity if mode is 'eval'
            list[torch.Tensor[float32]] : N x 1 x H x W disparity if mode is 'train'
        '''

        # Transform inputs
        image0, image1, \
            padding_top, padding_right = self.transform_inputs(image0, image1)

        outputs = self.model(
            left=image0,
            right=image1)

        if self.mode == 'eval':
            # Get finest output    
            if mode_type == "DM":
               output3 = torch.unsqueeze(outputs[2], dim=1)
               return output3
            # # If we padded the input, then crop
            # if padding_top != 0 or padding_right != 0:
            #     output3 = output3[..., padding_top:, :-padding_right]
            #return output3
            # 这里，我们使用代价卷作为输出
            elif mode_type=='CV':
                output3CV = torch.unsqueeze(outputs[3], dim=1)
                return output3CV

            

        elif self.mode == 'train':
            outputs = [
                torch.unsqueeze(output, dim=1) for output in outputs
            ]

            output1, output2, output3 = outputs

            # If we padded the input, then crop
            if padding_top != 0 or padding_right != 0:
                output1 = output1[..., padding_top:, :-padding_right]
                output2 = output2[..., padding_top:, :-padding_right]
                output3 = output3[..., padding_top:, :-padding_right]

            return (output1, output2, output3)

    def transform_inputs(self, image0, image1):
        '''
        Transforms the stereo pair using standard normalization as a preprocessing step

        Arg(s):
            image0 : torch.Tensor[float32]
                N x C x H x W left image
            image1 : torch.Tensor[float32]
                N x C x H x W right image
        Returns:
            torch.Tensor[float32] : N x 3 x H x W left image
            torch.Tensor[float32] : N x 3 x H x W right image
            int : padding applied to top of images
            int : padding applied to right of images
        '''

        # Dataset mean and standard deviations
        normal_mean_var = {
            'mean' : [0.485, 0.456, 0.406],
            'std' : [0.229, 0.224, 0.225]
        }

        transform_func = torchvision.transforms.Compose(
            [torchvision.transforms.Normalize(**normal_mean_var)])

        n_batch, _, n_height, n_width = image0.shape

        # Apply transform to each image pair
        image0 = torch.chunk(image0, chunks=n_batch, dim=0)
        image1 = torch.chunk(image1, chunks=n_batch, dim=0)

        image0 = torch.stack([
            transform_func(torch.squeeze(image)) for image in image0
        ], dim=0)
        image1 = torch.stack([
            transform_func(torch.squeeze(image)) for image in image1
        ], dim=0)

        # Pad to width and height such that it is divisible by 16
        if n_height % 16 != 0:
            times = n_height // 16
            padding_top = (times + 1) * 16 - n_height
        else:
            padding_top = 0

        if n_width % 16 != 0:
            times = n_width // 16
            padding_right = (times + 1) * 16 - n_width
        else:
            padding_right = 0

        # Pad the images and expand at 0-th dimension to get batch
        image0 = torch.nn.functional.pad(
            image0,
            (0, padding_right, padding_top, 0, 0, 0),
            mode='constant',
            value=0)

        image1 = torch.nn.functional.pad(
            image1,
            (0, padding_right, padding_top, 0, 0, 0),
            mode='constant',
            value=0)

        return image0, image1, padding_top, padding_right

    def compute_loss(self, outputs, ground_truth):
        '''
        Computes training loss

        Arg(s):
            outputs : list[torch.Tensor[float32]]
                list of N x 1 x H x W output disparity
            ground_truth : torch.Tensor[float32]
                N x 1 x H x W disparity
        Returns:
            float : loss
        '''

        mask = ground_truth > 0
        mask.detach_()

        # Select ground truth where disparity is defined
        ground_truth = ground_truth[mask]

        output1, output2, output3 = outputs

        # Select outputs where disparity is defined
        output1 = output1[mask]
        output2 = output2[mask]
        output3 = output3[mask]

        loss1 = torch.nn.functional.smooth_l1_loss(output1, ground_truth, size_average=True)
        loss2 = torch.nn.functional.smooth_l1_loss(output2, ground_truth, size_average=True)
        loss3 = torch.nn.functional.smooth_l1_loss(output3, ground_truth, size_average=True)

        loss = 0.5 * loss1 + 0.7 * loss2 + 1.0 * loss3

        return loss

    def parameters(self):
        '''
        Returns the list of parameters in the model

        Returns:
            list[torch.Tensor[float32]] : list of parameters
        '''

        return self.model.parameters()

    def named_parameters(self):
        '''
        Returns the list of named parameters in the model

        Returns:
            dict[str, torch.Tensor[float32]] : name, parameters pair
        '''

        return self.model.named_parameters()

    def train(self, flag_only=False):
        '''
        Sets model to training mode

        Arg(s):
            flag_only : bool
                if set, then only sets the train flag, but not mode
        '''

        if not flag_only:
            self.model.train()

        self.mode = 'train'

    def eval(self, flag_only=False):
        '''
        Sets model to evaluation mode

        Arg(s):
            flag_only : bool
                if set, then only sets the eval flag, but not mode
        '''

        if not flag_only:
            self.model.eval()

        self.mode = 'eval'

    def to(self, device):
        '''
        Moves model to device

        Arg(s):
            device : torch.device
                cpu or cuda device to run on
        '''

        # Move to device
        self.model.to(device)

    def save_model(self, save_path):
        '''
        Stores weights into a checkpoint

        Arg(s):
            save_path : str
                path to model weights
        '''

        checkpoint = {
            'state_dict' : self.model.state_dict()
        }

        torch.save(checkpoint, save_path)

    def restore_model(self, restore_path):
        '''
        Loads weights from checkpoint

        Arg(s):
            restore_path : str
                path to model weights
        '''

        self.model = torch.nn.DataParallel(self.model)
        state_dict = torch.load(restore_path)
        self.model.load_state_dict(state_dict['state_dict'])
