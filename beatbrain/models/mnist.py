import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset
import torchvision
from torchvision import transforms
import pytorch_lightning as pl

from cached_property import cached_property
from pathlib import Path
from more_itertools import interleave

from ..utils.config import Config
from ..utils import registry


class BasicConv2d(nn.Module):
    def __init__(self, in_channels, out_channels, **kwargs):
        super(BasicConv2d, self).__init__()
        # self.conv = nn.Conv2d(in_channels, out_channels, bias=False, **kwargs)  # Torchvision implementation doesn't use bias. Why?
        self.conv = nn.Conv2d(in_channels, out_channels, **kwargs)
        self.bn = nn.BatchNorm2d(out_channels, eps=0.001)

    def forward(self, x):
        x = self.conv(x)
        x = self.bn(x)
        return F.relu(x, inplace=True)


class Inception(nn.Module):
    def __init__(self, in_channels, pool_features):
        super().__init__()
        self.branch1x1 = BasicConv2d(in_channels, 64, kernel_size=1)

        self.branch5x5_1 = BasicConv2d(in_channels, 48, kernel_size=1)
        self.branch5x5_2 = BasicConv2d(48, 64, kernel_size=5, padding=2)

        self.branch3x3dbl_1 = BasicConv2d(in_channels, 64, kernel_size=1)
        self.branch3x3dbl_2 = BasicConv2d(64, 96, kernel_size=3, padding=1)
        self.branch3x3dbl_3 = BasicConv2d(96, 96, kernel_size=3, padding=1)

        self.branch_pool = BasicConv2d(in_channels, pool_features, kernel_size=1)

    def forward(self, x):
        branch1x1 = self.branch1x1(x)

        branch5x5 = self.branch5x5_1(x)
        branch5x5 = self.branch5x5_2(branch5x5)

        branch3x3dbl = self.branch3x3dbl_1(x)
        branch3x3dbl = self.branch3x3dbl_2(branch3x3dbl)
        branch3x3dbl = self.branch3x3dbl_3(branch3x3dbl)

        branch_pool = F.avg_pool2d(x, kernel_size=3, stride=1, padding=1)
        branch_pool = self.branch_pool(branch_pool)

        outputs = [branch1x1, branch5x5, branch3x3dbl, branch_pool]
        return torch.cat(outputs, 1)


@registry.register("model", "MNISTAutoencoder")
class MNISTAutoencoder(pl.LightningModule):
    def __init__(self, hparams: Config):
        super().__init__()
        self.hparams = hparams
        self.latent_dim = hparams.latent_dim

        self.encoder = nn.Sequential(
            nn.Conv2d(1, 16, 5, padding=2),
            nn.MaxPool2d(2),
            nn.ReLU(),
            nn.Conv2d(16, 8, 5, padding=2),
            nn.MaxPool2d(2),
            nn.ReLU(),
            nn.Conv2d(8, self.latent_dim, 5, padding=3),
            nn.MaxPool2d(2),
            nn.ReLU(),
        )

        self.decoder = nn.Sequential(
            nn.Conv2d(self.latent_dim, 8, 3, padding=1),
            nn.ReLU(),
            nn.UpsamplingNearest2d(scale_factor=2),
            nn.Conv2d(8, 8, 3, padding=1),
            nn.ReLU(),
            nn.UpsamplingNearest2d(scale_factor=2),
            nn.Conv2d(8, 16, 3),
            nn.ReLU(),
            nn.UpsamplingNearest2d(scale_factor=2),
            nn.Conv2d(16, 1, 3, padding=1),
            nn.Sigmoid(),
        )

    def encode(self, x):
        return self.encoder(x)

    def decode(self, x):
        return self.decoder(x)

    def forward(self, x):
        latent = self.encode(x)
        recon = self.decode(latent)
        return latent, recon

    def training_step(self, batch, batch_idx):
        x, y = batch
        latent, recon = self.forward(x)
        loss = F.binary_cross_entropy(recon, x)
        output = {"loss": loss}
        if batch_idx % 100 == 0:
            tensorboard_logs = {"loss/train_loss": loss}
            output["log"] = tensorboard_logs
        return output

    def validation_step(self, batch, batch_idx):
        x, y = batch
        latent, recon = self.forward(x)
        loss = F.binary_cross_entropy(recon, x)
        output = {"val_loss": loss}
        if batch_idx % 100 == 0:
            output["x"] = x[0]
            output["recon"] = recon[0]
            output["latent"] = latent[0]
        return output

    def validation_epoch_end(self, outputs):
        recon_images = list(
            interleave(
                filter(
                    lambda x: x is not None, [output.get("x") for output in outputs]
                ),
                filter(
                    lambda x: x is not None, [output.get("recon") for output in outputs]
                ),
            )
        )
        recon_grid = torchvision.utils.make_grid(recon_images, nrow=4, normalize=False)
        self.logger.experiment.add_image(
            f"reconstruction", recon_grid, self.current_epoch
        )
        avg_loss = torch.stack([x["val_loss"] for x in outputs]).mean()
        tensorboard_logs = {"loss/val_loss": avg_loss}
        return {"val_loss": avg_loss, "log": tensorboard_logs}

    @cached_property
    def default_train_transform(self):
        return transforms.Compose(
            [
                # transforms.Resize(256),
                transforms.ToTensor(),
            ]
        )

    def prepare_data(self):
        """
        Split train dataset into train and val (80-20)
        """
        train_ratio = 0.8
        train_dataset = torchvision.datasets.FashionMNIST(
            self.hparams.data_root,
            train=True,
            download=True,
            transform=self.default_train_transform,
        )
        num_train_samples = len(train_dataset)
        train_indices = list(range(0, int(train_ratio * num_train_samples)))
        val_indices = list(
            range(int(train_ratio * num_train_samples), num_train_samples)
        )
        self.train_dataset = Subset(train_dataset, train_indices)
        self.val_dataset = Subset(train_dataset, val_indices)
        self.test_dataset = torchvision.datasets.FashionMNIST(
            self.hparams.data_root,
            train=False,
            download=True,
            transform=self.default_train_transform,
        )

    def train_dataloader(self):
        return DataLoader(
            self.train_dataset,
            batch_size=self.hparams.batch_size,
            shuffle=True,
            pin_memory=True,
        )

    def val_dataloader(self):
        return DataLoader(
            self.val_dataset,
            batch_size=self.hparams.batch_size,
            shuffle=False,
            pin_memory=True,
        )

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=self.hparams.learning_rate)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, patience=3, threshold=1e-3, verbose=True,
        )
        return [optimizer], [scheduler]
