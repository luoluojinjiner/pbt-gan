#-*- coding: utf-8 -*-
from __future__ import division
import os
import time
import tensorflow as tf
import numpy as np
import scipy.misc

from ops import *
from utils import *
from inception import *

class GAN(object):
    model_name = "GAN"     # name for checkpoint

    def __init__(self, worker_idx=-1, batch_size=128, z_dim=100, epochs=100):
        self.worker_idx = worker_idx
        self.batch_size = batch_size
        self.z_dim = z_dim
        self.epochs = epochs

        # parameters
        self.input_height = 32
        self.input_width = 32
        self.output_height = 32
        self.output_width = 32

        self.z_dim = z_dim  # dimension of noise-vector
        self.c_dim = 3  # color dimension

        # train
        self.learning_rate_D = tf.get_variable('learning_rate_D'.format(worker_idx), initializer=tf.constant(1e-4))
        self.learning_rate_G = tf.get_variable('learning_rate_G'.format(worker_idx), initializer=tf.constant(1e-4))

        self.beta1 = 0.5

        # test
        self.sample_num = 64  # number of generated images to be saved

        # load cifar10
        self.data_X, self.data_y = load_cifar10('cifar10')
        print("Shape of cifar10 X: {}".format(self.data_X.shape))
        print("Shape of cifar10 Y: {}".format(self.data_y.shape))

        # get number of batches for a single epoch
        self.num_batches = len(self.data_X) // self.batch_size

        # graph inputs for visualize training results
        self.sample_z = np.random.uniform(-1, 1, size=(self.batch_size , self.z_dim))

        # load pretrained inception network (code from tensorflow / openAI)
        self.init_inception()

    def discriminator(self, x, is_training=True, reuse=False):
        # Network Architecture is exactly same as in infoGAN (https://arxiv.org/abs/1606.03657)
        # Architecture : (64)4c2s-(128)4c2s_BL-FC1024_BL-FC1_S
        with tf.variable_scope("discriminator".format(self.worker_idx), reuse=reuse):

            net = lrelu(conv2d(x, 64, 5, 5, 2, 2, name='d_conv1'))
            net = lrelu(bn(conv2d(net, 128, 5, 5, 2, 2, name='d_conv2'), is_training=is_training, scope='d_bn2'))
            net = lrelu(bn(conv2d(net, 256, 5, 5, 2, 2, name='d_conv3'), is_training=is_training, scope='d_bn3'))
            net = lrelu(bn(conv2d(net, 512, 5, 5, 2, 2, name='d_conv4'), is_training=is_training, scope='d_bn4'))
            net = tf.reshape(net, [self.batch_size, -1])
            out_logit = linear(net, 1, scope='d_fc5')
            out = tf.nn.sigmoid(out_logit)

            return out, out_logit, net

    def generator(self, z, is_training=True, reuse=False):
        # Network Architecture is exactly same as in infoGAN (https://arxiv.org/abs/1606.03657)
        # Architecture : FC1024_BR-FC7x7x128_BR-(64)4dc2s_BR-(1)4dc2s_S
        with tf.variable_scope("generator".format(self.worker_idx), reuse=reuse):

            h_size = 32
            h_size_2 = 16
            h_size_4 = 8
            h_size_8 = 4
            h_size_16 = 2

            net = linear(z, 512*h_size_16*h_size_16, scope='g_fc1')
            net = tf.nn.relu(bn(tf.reshape(net, [self.batch_size, h_size_16, h_size_16, 512]),is_training=is_training, scope='g_bn1'))
            net = tf.nn.relu(bn(deconv2d(net, [self.batch_size, h_size_8, h_size_8, 256], 5, 5, 2, 2, name='g_dc2'), is_training=is_training, scope='g_bn2'))
            net = tf.nn.relu(bn(deconv2d(net, [self.batch_size, h_size_4, h_size_4, 128], 5, 5, 2, 2, name='g_dc3'), is_training=is_training, scope='g_bn3'))
            net = tf.nn.relu(bn(deconv2d(net, [self.batch_size, h_size_2, h_size_2, 64], 5, 5, 2, 2, name='g_dc4'),is_training=is_training, scope='g_bn4'))
            out = tf.nn.tanh(deconv2d(net, [self.batch_size, self.output_height, self.output_width, self.c_dim], 5, 5, 2, 2, name='g_dc5'))

        return out

    def build_model(self):
        # some parameters
        image_dims = [self.input_height, self.input_width, self.c_dim]
        bs = self.batch_size

        """ Graph Input """
        # images
        self.inputs = tf.placeholder(tf.float32, [bs] + image_dims, name='real_images')

        # noises
        self.z = tf.placeholder(tf.float32, [bs, self.z_dim], name='z')

        """ Loss Function """

        # output of D for real images
        D_real, D_real_logits, _ = self.discriminator(self.inputs, is_training=True, reuse=False)

        # output of D for fake images
        G = self.generator(self.z, is_training=True, reuse=False)
        D_fake, D_fake_logits, _ = self.discriminator(G, is_training=True, reuse=True)

        # get loss for discriminator
        d_loss_real = tf.reduce_mean(
            tf.nn.sigmoid_cross_entropy_with_logits(logits=D_real_logits, labels=tf.ones_like(D_real)))
        d_loss_fake = tf.reduce_mean(
            tf.nn.sigmoid_cross_entropy_with_logits(logits=D_fake_logits, labels=tf.zeros_like(D_fake)))

        self.d_loss = d_loss_real + d_loss_fake

        # get loss for generator
        self.g_loss = tf.reduce_mean(
            tf.nn.sigmoid_cross_entropy_with_logits(logits=D_fake_logits, labels=tf.ones_like(D_fake)))

        """ Training """
        # divide trainable variables into a group for D and a group for G
        t_vars = tf.trainable_variables()
        d_vars = [var for var in t_vars if 'd_' in var.name]
        g_vars = [var for var in t_vars if 'g_' in var.name]

        # optimizers
        with tf.control_dependencies(tf.get_collection(tf.GraphKeys.UPDATE_OPS)):
            self.d_optim = tf.train.AdamOptimizer(self.learning_rate_D, beta1=self.beta1).minimize(self.d_loss, var_list=d_vars)
            self.g_optim = tf.train.AdamOptimizer(self.learning_rate_G, beta1=self.beta1).minimize(self.g_loss, var_list=g_vars)

        """" Testing """
        # for test
        self.fake_images = self.generator(self.z, is_training=False, reuse=True)

        """ Summary """
        d_loss_real_sum = tf.summary.scalar("d_loss_real", d_loss_real)
        d_loss_fake_sum = tf.summary.scalar("d_loss_fake", d_loss_fake)
        d_loss_sum = tf.summary.scalar("d_loss", self.d_loss)
        g_loss_sum = tf.summary.scalar("g_loss", self.g_loss)

        # final summary operations
        self.g_sum = tf.summary.merge([d_loss_fake_sum, g_loss_sum])
        self.d_sum = tf.summary.merge([d_loss_real_sum, d_loss_sum])

    def step(self, idx, epoch):

        start_time = time.time()

        # number of updates of D for a G
        update_num_D = 1 #5
        
        for i in range(update_num_D):
            batch_images = self.data_X[idx*self.batch_size:(idx+1)*self.batch_size]
            batch_z = np.random.uniform(-1, 1, [self.batch_size, self.z_dim]).astype(np.float32)

            _, summary_str, d_loss = self.mon_sess.run([self.d_optim, self.d_sum, self.d_loss],
                                           feed_dict={self.inputs: batch_images, self.z: batch_z})

        # update G network
        _, summary_str, g_loss = self.mon_sess.run([self.g_optim, self.g_sum, self.g_loss], feed_dict={self.z: batch_z})


        # display training status
        print("Epoch: [%2d] [%4d/%4d] time: %4.4f, d_loss: %.8f, g_loss: %.8f" \
              % (epoch, idx, self.num_batches, time.time() - start_time, d_loss, g_loss))

    def eval(self):
        """evaluate the inception score"""

        samples = self.mon_sess.run(self.fake_images, feed_dict={self.z: self.sample_z})
        tot_num_samples = min(self.sample_num, self.batch_size)
        manifold_h = int(np.floor(np.sqrt(tot_num_samples)))
        manifold_w = int(np.floor(np.sqrt(tot_num_samples)))
        images = samples[:manifold_h * manifold_w, :, :, :]
        images = scipy.misc.bytescale(rescale(images))

        images = list(images)
        np.save('image', images)
        print(self.get_inception_score(images))

    def init_inception(self):
        return init_inception(self)

    def get_inception_score(self, images):
        return get_inception_score(self, images)


