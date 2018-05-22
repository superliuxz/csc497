import cv2
import logging
import os
import tarfile

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from skimage import transform
from sklearn.model_selection import KFold
import tensorflow as tf


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
tf.logging.set_verbosity(tf.logging.INFO)


class CNN:
    def __init__(self, **kwargs):
        self.opt_conv = kwargs.get('opt_conv')

        self.train_data, self.train_label \
            = self.load_data(kwargs.get('input'), kwargs.get('label'), random_rotate=kwargs.get('random_rotate'))
        
        self.img_h = self.train_data.shape[1]
        self.img_w = self.train_data.shape[2]

        self.conv1_filter_depth = kwargs.get('conv1_depth')

        self.conv2_filter_depth = kwargs.get('conv2_depth')

        self.fc_feat_size = kwargs.get('fc_feat')

        self.learning_rate = kwargs.get('lr')
        self.conv1_size = kwargs.get('conv1_size')
        self.conv2_size = kwargs.get('conv2_size')

        self.alpha = kwargs.get('regularization')

        tf.reset_default_graph()

        self.result = pd.DataFrame()

        self.x = tf.placeholder(tf.float32, [None, self.img_h, self.img_w, 3], name='x')
        self.y = tf.placeholder(tf.float32, [None, 512], name='y')
        self.keep_prob = tf.placeholder(tf.float32, name='keep_prob')
        self.is_training = tf.placeholder(tf.bool, name='is_training')

        # 1st conv layer
        self.w1 = tf.Variable(tf.truncated_normal([self.conv1_size, self.conv1_size, 3, self.conv1_filter_depth], stddev=0.1), name='w1')
        self.b1 = tf.Variable(tf.constant(0.1, shape=[self.conv1_filter_depth]), name='b1')
        # (-1, self.img_h, self.img_w, self.conv1_filter_depth)
        self.h1 = tf.nn.relu(
            tf.layers.batch_normalization(
                tf.nn.conv2d(self.x, self.w1, strides=[1, 1, 1, 1], padding='SAME') + self.b1, training=self.is_training), name='h1')
        self.print_h1 = tf.Print(self.h1, [self.h1], 'h1: ')

        # 1st pooling layer
        # (-1, self.img_h/2, self.img_w/2, self.conv1_filter_depth)
        self.pool1 = tf.nn.max_pool(self.h1, ksize=[1, 2, 2, 1], strides=[1, 2, 2, 1], padding='SAME', name='pool1')

        # 2nd conv layer
        self.w2 = tf.Variable(tf.truncated_normal(
            [self.conv2_size, self.conv2_size, self.conv1_filter_depth, self.conv2_filter_depth], stddev=0.1), name='w2')
        self.b2 = tf.Variable(tf.constant(0.1, shape=[self.conv2_filter_depth]), name='b2')
        # (-1, self.img_h/2, self.img_w/2, self.conv2_filter_depth)
        self.h2 = tf.nn.relu(
            tf.layers.batch_normalization(
                tf.nn.conv2d(self.pool1, self.w2, strides=[1, 1, 1, 1], padding='SAME') + self.b2, training=self.is_training), name='h2')
        self.print_h2 = tf.Print(self.h2, [self.h2], 'h2: ')

        # 2nd pooling layer
        # (-1, self.img_h/4,self.w/4, self.conv2_filter_depth)
        self.pool2 = tf.nn.max_pool(self.h2, ksize=[1, 2, 2, 1], strides=[1, 2, 2, 1], padding='SAME', name='pool2')

        final_conv_d = self.conv2_filter_depth
        final_pool = self.pool2

        # optional conv layers without pooling
        if self.opt_conv > 0:
            self.w_opt1 = tf.Variable(tf.truncated_normal(
                [self.conv2_size, self.conv2_size, self.conv2_filter_depth, self.conv2_filter_depth*2], stddev=0.1),
                name='w_opt1')
            self.b_opt1 = tf.Variable(tf.constant(0.1, shape=[self.conv2_filter_depth*2]), name='b_opt1')
            self.h_opt1 = tf.nn.relu(
                tf.layers.batch_normalization(
                    tf.nn.conv2d(self.pool2, self.w_opt1, strides=[1, 1, 1, 1], padding='SAME') + self.b_opt1),
                name='h_opt1')
            final_conv_d = self.conv2_filter_depth*2
            final_pool = self.h_opt1

        if self.opt_conv > 1:
            self.w_opt2 = tf.Variable(tf.truncated_normal(
                [self.conv2_size, self.conv2_size, self.conv2_filter_depth*2, self.conv2_filter_depth*4], stddev=0.1),
                name='w_opt2')
            self.b_opt2 = tf.Variable(tf.constant(0.1, shape=[self.conv2_filter_depth*4]), name='b_opt2')
            self.h_opt2 = tf.nn.relu(
                tf.layers.batch_normalization(
                    tf.nn.conv2d(self.h_opt1, self.w_opt2, strides=[1, 1, 1, 1], padding='SAME') + self.b_opt2),
                name='h_opt2')
            final_conv_d = self.conv2_filter_depth*4
            final_pool = self.h_opt2

        # fc1
        # after twice pooling (downsampling), (h,w) -> (h/4,w/4)
        self.w3 = tf.Variable(tf.truncated_normal(
            [self.img_h//4*self.img_w//4*final_conv_d, self.fc_feat_size], stddev=0.1), name='w3')
        self.b3 = tf.Variable(tf.constant(0.1, shape=[self.fc_feat_size]), name='b3')
        self.flat_pool2 = tf.reshape(
            final_pool, [-1, self.img_h//4*self.img_w//4*final_conv_d], name='flat_pool2')
        # (-1, self.fc_feat_size)
        self.h3 = tf.nn.relu(tf.matmul(self.flat_pool2, self.w3) + self.b3, name='h3')
        self.h3_drop = tf.layers.dropout(self.h3, 1.0-self.keep_prob, name='h3_drop', training=self.is_training)

        # # fc2, output
        # self.w4 = tf.Variable(tf.truncated_normal([self.fc_feat_size, 512], stddev=0.1), name='w4')
        # self.b4 = tf.Variable(tf.constant(0.1, shape=[512]), name='b4')
        # # (-1, 512)
        # self.y_pred = tf.add(tf.matmul(self.h3_drop, self.w4), self.b4, name='y_pred')

        # self.MSE = tf.losses.mean_squared_error(self.y, self.y_pred)

        self.MSE = tf.losses.mean_squared_error(self.y, self.h3_drop)

        self.training_step = tf.train.AdamOptimizer(self.learning_rate)\
            .minimize(self.MSE + self.alpha*tf.nn.l2_loss(self.w1) + self.alpha*tf.nn.l2_loss(self.w2)
                      + self.alpha*tf.nn.l2_loss(self.w3),
                      name='training_step')

        # r2 coeff. of correl.
        # residuals = tf.reduce_sum(tf.square(tf.subtract(self.y, self.y_pred)))
        residuals = tf.reduce_sum(tf.square(tf.subtract(self.y, self.h3_drop)))
        total = tf.reduce_sum(tf.square(tf.subtract(self.y, tf.reduce_mean(self.y))))
        self.r_square = tf.subtract(1.0, tf.div(residuals, total), name='r_square')

        self.saver = tf.train.Saver()

    def train(self, **kwargs):
        batch_size = kwargs.get('batch_size')
        split_batch = kwargs.get('CV_batch')
        split_shuffle = kwargs.get('split_shuffle')
        keep_prob = kwargs.get('keep_prob')
        iter = kwargs.get('iter')
        model_name = kwargs.get('save_model_name')

        logger.info(f'start training...')
        with tf.Session() as sess:
            sess.run(tf.global_variables_initializer())
            for i in range(iter):
                for cv_idx, (train_data, train_label, valid_data, valid_label) in \
                        enumerate(self.split_train_valid(n=split_batch, shuffle=split_shuffle)):
                    batch_idx = 0
                    while batch_idx < train_data.shape[0]:
                        x_batch = train_data[batch_idx:batch_idx+batch_size, :, :, :]
                        y_batch = train_label[batch_idx:batch_idx+batch_size, :]

                        MSE, *_ = sess.run([self.MSE, self.training_step], feed_dict={
                            self.x: x_batch,
                            self.y: y_batch,
                            self.keep_prob: keep_prob,
                            self.is_training: False
                        })
                        coeff_correl = self.r_square.eval(feed_dict={
                            self.x: valid_data,
                            self.y: valid_label,
                            self.keep_prob: 1.0,
                            self.is_training: False
                        })
                        logger.info(f'cv {cv_idx} batch {batch_idx}, '
                                    f'MSE: {MSE}, '
                                    f'coefficient of correlation : {coeff_correl}')
                        self.result = self.result.append({'MSE': MSE, 'coeff_correl': coeff_correl},
                                                         ignore_index=True
                                                         )
                        batch_idx += batch_size
            self.saver.save(sess, os.path.join(os.getcwd(), model_name))
        self.result.to_csv('result.csv', index=False)
        logger.info(f'done')

    def pred_test_img(self, model_name):
        with self.restore_session(model_name) as sess:
            plt.figure(figsize=(15, 8))
            for i, im in enumerate(['test.jpg', 'test2.jpg', 'test3.jpg']):
                img = cv2.imread(im, flags=cv2.IMREAD_ANYCOLOR)
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                oimg = cv2.resize(src=img, dsize=(self.img_w, self.img_h), interpolation=cv2.INTER_LANCZOS4)
                img = oimg.reshape(-1, self.img_h, self.img_w, 3)
                # img = (img-self.train_data.mean())/self.train_data.std()
                img = img/self.train_data.max()

                res = self.h3.eval(session=sess, feed_dict={
                    self.x: img,
                    self.keep_prob: 1.0,
                    self.is_training: False
                })
                res = np.reshape(res, (16, 32))

                plt.subplot(2, 3, 1+i)
                plt.imshow(oimg)
                plt.subplot(2, 3, 4+i)
                plt.imshow(res, cmap='binary')
            plt.show()

    def load_data(self, dataset, label, random_rotate) -> (np.array, np.array, np.array):
        train = []
        with tarfile.open(dataset) as tar:
            for f in tar.getmembers():
                bimg = np.array(bytearray(tar.extractfile(f).read()), dtype=np.uint8)
                img = cv2.imdecode(bimg, flags=cv2.IMREAD_ANYCOLOR)
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                train.append(img)
        train_data = np.array(train)
        train_label = np.genfromtxt(label, delimiter=',')

        if random_rotate > 0:
            logger.info(f'rotate img...')
            rot_data = []
            for _ in range(random_rotate):
                for im in train_data:
                    rim = transform.rotate(im, np.random.randint(0, 360), order=3, preserve_range=True)
                    rot_data.append(rim)
                train_label = np.vstack((train_label, train_label))
            rot_data = np.array(rot_data)
            train_data = np.vstack((train_data, rot_data))
            logger.info(f'done')

        logger.info(f'normalize data...')
        # train_data = (train_data-train_data.mean())/train_data.std()
        train_data = train_data/train_data.max()
        logger.info(f'done')

        logger.info(f'training data: {train_data.shape}')
        logger.info(f'train label: {train_label.shape}')

        return train_data, train_label

    def split_train_valid(self, n: int=10, shuffle: bool=True):
        """
        10-fold CV
        """
        kfold = KFold(n_splits=n, shuffle=shuffle, random_state=1)
        for train_idx, valid_idx in kfold.split(self.train_data, self.train_label):
            yield self.train_data[train_idx], self.train_label[train_idx], \
                  self.train_data[valid_idx], self.train_label[valid_idx]

    def plot_training_result(self):
        if len(self.result) == 0:
            self.result = pd.read_csv('result.csv', header=0)
        plt.figure(figsize=(10, 5))
        plt.subplot(1, 2, 1)
        plt.plot(np.arange(0, len(self.result)), self.result['MSE'])
        plt.title('MSE')
        plt.xlabel('training steps')

        plt.subplot(1, 2, 2)
        plt.plot(np.arange(0, len(self.result)), self.result['coeff_correl'])
        plt.title('R^2')
        plt.xlabel('training steps')
        plt.show()

    def plot_activation(self, model_name):
        """
        plot the activation function for conv and pool
        """
        def closest_factors(n: int) -> (int, int):
            x = int(np.sqrt(n))
            while n % x != 0:
                x -= 1
            return (x, n//x) if x > n else (n//x, x)
        img_num = np.random.randint(0, self.train_data.shape[0]-1)
        logger.info('recompute activations for different layers ...')
        with self.restore_session(model_name) as sess:
            h1 = self.h1.eval(session=sess, feed_dict={
                self.x: self.train_data[img_num: img_num+1],
                self.keep_prob: 1.0,
                self.is_training: False
            })
            pool1 = self.pool1.eval(session=sess, feed_dict={
                self.x: self.train_data[img_num: img_num+1],
                self.keep_prob: 1.0,
                self.is_training: False
            })
            h2 = self.h2.eval(session=sess, feed_dict={
                self.x: self.train_data[img_num: img_num + 1],
                self.keep_prob: 1.0,
                self.is_training: False
            })
            pool2 = self.pool2.eval(session=sess, feed_dict={
                self.x: self.train_data[img_num: img_num + 1],
                self.keep_prob: 1.0,
                self.is_training: False
            })
            h3 = self.h3.eval(session=sess, feed_dict={
                self.x: self.train_data[img_num: img_num + 1],
                self.keep_prob: 1.0,
                self.is_training: False
            })
        logger.info('done')

        plt.figure(figsize=(15, 10))
        # original image
        plt.subplot(2, 3, 1)
        plt.title(f'original image (normalized)')
        plt.imshow(self.train_data[img_num])

        c1_h, c1_w = closest_factors(self.conv1_filter_depth)
        c2_h, c2_w = closest_factors(self.conv2_filter_depth)
        fc_h, fc_w = closest_factors(self.fc_feat_size)

        # conv1
        plt.subplot(2, 3, 2)
        plt.title(f'conv1 {h1.shape}')
        h1 = np.reshape(h1, (-1, self.img_h, self.img_w, c1_h, c1_w))
        h1 = np.transpose(h1, (0, 3, 1, 4, 2))
        h1 = np.reshape(h1, (-1, c1_h*self.img_h, c1_w*self.img_w))
        plt.imshow(h1[0])

        # pool1
        plt.subplot(2, 3, 3)
        plt.title(f'pool1 {pool1.shape}')
        pool1 = np.reshape(pool1, (-1, self.img_h//2, self.img_w//2, c1_h, c1_w))
        pool1 = np.transpose(pool1, (0, 3, 1, 4, 2))
        pool1 = np.reshape(pool1, (-1, c1_h*self.img_h//2, c1_w*self.img_w//2))
        plt.imshow(pool1[0])

        # conv2
        plt.subplot(2, 3, 4)
        plt.title(f'conv2 {h2.shape}')
        h2 = np.reshape(h2, (-1, self.img_h//2, self.img_w//2, c2_h, c2_w))
        h2 = np.transpose(h2, (0, 3, 1, 4, 2))
        h2 = np.reshape(h2, (-1, c2_h*self.img_h//2, c2_w*self.img_w//2))
        plt.imshow(h2[0], cmap='binary')

        # pool2
        plt.subplot(2, 3, 5)
        plt.title(f'pool2 {pool2.shape}')
        pool2 = np.reshape(pool2, (-1, self.img_h//4, self.img_w//4, c2_h, c2_w))
        pool2 = np.transpose(pool2, (0, 3, 1, 4, 2))
        pool2 = np.reshape(pool2, (-1, c2_h*self.img_h//4, c2_w*self.img_w//4))
        plt.imshow(pool2[0], cmap='binary')

        # fc1
        plt.subplot(2, 3, 6)
        plt.title(f'fc1 {h3.shape}')
        h3 = np.reshape(h3, (-1, fc_w, fc_h))
        plt.imshow(h3[0], cmap='binary')

        plt.show()

    def restore_session(self, model_name) -> tf.Session:
        tf.reset_default_graph()
        saver = tf.train.import_meta_graph(os.path.join(os.getcwd(), f'{model_name}.meta'))
        session = tf.Session()
        self.reload_tensors(tf.get_default_graph())
        saver.restore(session, tf.train.latest_checkpoint('./'))
        return session

    def reload_tensors(self, graph: tf.Graph):
        self.x = graph.get_tensor_by_name('x:0')
        self.y = graph.get_tensor_by_name('y:0')
        self.keep_prob = graph.get_tensor_by_name('keep_prob:0')
        self.is_training = graph.get_tensor_by_name('is_training:0')

        self.w1 = graph.get_tensor_by_name('w1:0')
        self.b1 = graph.get_tensor_by_name('b1:0')
        self.h1 = graph.get_tensor_by_name('h1:0')
        self.pool1 = graph.get_tensor_by_name('pool1:0')

        self.w2 = graph.get_tensor_by_name('w2:0')
        self.b2 = graph.get_tensor_by_name('b2:0')
        self.h2 = graph.get_tensor_by_name('h2:0')
        self.pool2 = graph.get_tensor_by_name('pool2:0')

        self.w3 = graph.get_tensor_by_name('w3:0')
        self.b3 = graph.get_tensor_by_name('b3:0')
        self.flat_pool2 = graph.get_tensor_by_name('flat_pool2:0')
        self.h3 = graph.get_tensor_by_name('h3:0')

        if self.opt_conv > 0:
            self.w_opt1 = graph.get_tensor_by_name('w_opt1:0')
            self.b_opt1 = graph.get_tensor_by_name('b_opt1:0')
            self.h_opt1 = graph.get_tensor_by_name('h_opt1:0')

        if self.opt_conv > 1:
            self.w_opt2 = graph.get_tensor_by_name('w_opt2:0')
            self.b_opt2 = graph.get_tensor_by_name('b_opt2:0')
            self.h_opt2 = graph.get_tensor_by_name('h_opt2:0')

        # self.w4 = graph.get_tensor_by_name('w4:0')
        # self.b4 = graph.get_tensor_by_name('b4:0')
        # self.y_pred = graph.get_tensor_by_name('y_pred:0')

        self.training_step = graph.get_operation_by_name('training_step')
        self.r_square = graph.get_tensor_by_name('r_square:0')


if __name__ == '__main__':
    kw = {
        'conv1_depth': 16,
        'conv1_size': 3,
        'conv2_depth': 32,
        'conv2_size': 3,
        'fc_feat': 512,
        'lr': 10**-3,
        'regularization': 0.01,
        'input': '5000.256x192.tar.bz2',
        'label': '5000.label.txt',
        'opt_conv': 0,
        'random_rotate': 0,
    }

    cnn = CNN(**kw)

    train_param = {
        'keep_prob': 1,
        'CV_batch': 10,
        'split_shuffle': True,
        'batch_size': 50,
        'iter': 4,
        'save_model_name': '5000_synth_keepprob1'
    }

    cnn.train(**train_param)
    cnn.plot_activation(train_param.get('save_model_name'))
    cnn.pred_test_img(train_param.get('save_model_name'))