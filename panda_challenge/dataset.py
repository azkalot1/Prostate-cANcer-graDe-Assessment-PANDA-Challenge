import numpy as np
import os
from pytorch_toolbelt.utils.torch_utils import tensor_from_rgb_image
import albumentations as A
from torch.utils.data import Dataset
import torch
import openslide
from .utils import crop_around_mask, tile
import json
import pandas as pd


class ClassifcationDatasetSimpleTrain(Dataset):

    CROP_HIGHEST_ZOOM = 32

    def __init__(
        self,
        data_df,
        transforms_json,
        image_dir,
        mask_dir,
            crop_size=512):
        """Prepares pytorch dataset for training
        Crops around mask and returns ISUP score for the slide

        Args:
            data_df (pd.DataFrame): data.frame with slides id and labels.
            augmentations (albumentations.compose): augmentations.
            image_dir (str): folder with images.
            mask_dir (str): folder with masks.
            crop_size(int): crop size around mask. Default: 512
        Returns
            Dataset

        """
        self.data_df = pd.read_csv(data_df)
        self.image_dir = image_dir
        self.mask_dir = mask_dir
        self.crop_size = crop_size
        self.transforms = self._get_aug(transforms_json)

    def _get_aug(self, arg):
        with open(arg) as f:
            return A.from_dict(json.load(f))

    def __len__(self):
        return(len(self.data_df))

    def __getitem__(self, idx):
        """Will load the mask, get random coordinates around/with the mask,
        load the image by coordinates
        """
        slide_id = self.data_df.image_id.values[idx]
        isup_grade = self.data_df.isup_grade.values[idx]
        mask = openslide.OpenSlide(os.path.join(
            self.mask_dir,
            f'{slide_id}_mask.tiff'))
        image = openslide.OpenSlide(os.path.join(
            self.image_dir,
            f'{slide_id}.tiff'))
        k = int(mask.level_downsamples[-1])
        mask_full = mask.read_region(
            (0, 0),
            mask.level_count - 1,
            mask.level_dimensions[-1])
        mask_full = np.asarray(mask_full).astype(bool)[..., 0]
        crop_coords = crop_around_mask(
            mask_full,
            ClassifcationDatasetSimpleTrain.CROP_HIGHEST_ZOOM,
            ClassifcationDatasetSimpleTrain.CROP_HIGHEST_ZOOM)
        image_slice = image.read_region(
            (k*crop_coords['x_min'], k*crop_coords['y_min']),
            0,
            (self.crop_size, self.crop_size))
        mask_slice = mask.read_region(
            (k*crop_coords['x_min'], k*crop_coords['y_min']),
            0,
            (self.crop_size, self.crop_size))
        mask_slice = np.asarray(mask_slice)[..., 0]
        # if all < 2: isup_grade == 0
        if (mask_slice < 2).all():
            isup_grade = 0
        image_slice = np.asarray(image_slice)[..., :3]
        augmented = self.transforms(image=image_slice)
        image = augmented['image']
        # Add grade change based on mask values
        # If only 1,0 in mask we pass grade = 0
        # If there is cancer tissue, we pass isup_grade from labeling
        data = {'features': tensor_from_rgb_image(image).float(),
                'targets': torch.tensor(isup_grade)}
        return(data)


class ClassifcationDatasetMultiCrop(Dataset):
    def __init__(
        self,
        data_df,
        transforms_json,
        image_dir,
        mask_dir,
        mean=np.array([0.90949707, 0.8188697, 0.87795304]),
        std=np.array([0.36357649, 0.49984502, 0.40477625]),
        N=16,
            crop_size=512):
        """Prepares pytorch dataset for training
        Generates tiles from coarse slide and returns it

        Args:
            data_df (pd.DataFrame): data.frame with slides id and labels.
            augmentations (albumentations.compose): augmentations.
            image_dir (str): folder with images.
            mask_dir (str): folder with masks.
            crop_size(int): crop size around mask. Default: 512
        Returns
            Dataset

        """
        self.data_df = pd.read_csv(data_df)
        self.image_dir = image_dir
        self.mask_dir = mask_dir
        self.crop_size = crop_size
        self.N = N
        self.transforms = self._get_aug(transforms_json)
        self.mean = mean
        self.std = std

    def _get_aug(self, arg):
        with open(arg) as f:
            augs = A.from_dict(json.load(f))
        target = {}
        for i in range(1, self.N):
            target['image' + str(i)] = 'image'
        return A.Compose(augs, p=1, additional_targets=target)

    def __len__(self):
        return(len(self.data_df))

    def __getitem__(self, idx):
        """Will load the mask, get random coordinates around/with the mask,
        load the image by coordinates
        """
        slide_id = self.data_df.image_id.values[idx]
        isup_grade = self.data_df.isup_grade.values[idx]
        mask = openslide.OpenSlide(os.path.join(
            self.mask_dir,
            f'{slide_id}_mask.tiff'))
        image = openslide.OpenSlide(os.path.join(
            self.image_dir,
            f'{slide_id}.tiff'))
        mask = mask.read_region(
            (0, 0),
            mask.level_count - 1,
            mask.level_dimensions[-1])
        img = image.read_region(
            (0, 0),
            image.level_count - 1,
            image.level_dimensions[-1])
        img = np.asarray(img)[..., :3]
        mask = np.asarray(mask)[..., :3]
        tiles = tile(img, mask=mask, N=self.N)
        tiled_images = (1.0 - tiles['img']/255.0)
        tiled_images = (tiled_images - self.mean)/self.std
        assert len(tiled_images) == self.N
        target_names = ['image' + str(i) if i > 0 else 'image'
                        for i in range(len(tiled_images))]
        tiled_images = dict(zip(
            target_names,
            tiled_images))
        augmented = self.transforms(**tiled_images)

        tiled_images = [augmented[target] for target in target_names]
        tiled_images = np.stack(tiled_images).transpose(0, 3, 1, 2)
        data = {'features': torch.from_numpy(tiled_images).float(),
                'targets': torch.tensor(isup_grade)}
        return(data)
