# Copyright (c) 2016, NVIDIA CORPORATION. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#  * Neither the name of NVIDIA CORPORATION nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY
# OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import time, sys, cv2, os, time
import numpy as np
from Config import Config 
from ale_python_interface import ALEInterface
from python_speech_features import mfcc
from scipy.io.wavfile import read, write
from scipy.misc import imresize
import matplotlib.pyplot as plt


class GameManager:
    def __init__(self, game_name, display):
        self.game_name                        = game_name
        self.display                          = display
        self.ale                              = ALEInterface()
        self.ale.setInt("random_seed", np.random.randint(low=0, high=999, size=1)[0])# when confident in results
        self.ale.setInt("frame_skip", 4)# NOTE Do not change
        self.ale.setBool("display_screen", True)
        self.ale.setBool("sound", Config.USE_AUDIO)
        self.ale.loadROM('../../environment/Arcade-Learning-Environment-Audio/game/' + game_name + '.bin')
        self.screen_width, self.screen_height = self.ale.getScreenDims()
        self.legal_actions                    = self.ale.getMinimalActionSet()
        if Config.USE_AUDIO == True: 
            self.last_valid_image             = None
            self.last_valid_audio             = None

        self.reset()

    def _preprocess_image(self, image):
        # Preprocess raw image to 80*80 gray image
        image = cv2.cvtColor(cv2.resize(image, (84, 100)), cv2.COLOR_BGR2GRAY)
        image = image[26-20:110-20,:] # Crops to an 84x84 middle region, as in DQN paper
        image = image/128. - 1. # normalize
        #  image = cv2.cvtColor(cv2.resize(image, (84, 110)), cv2.COLOR_BGR2GRAY)

        return np.reshape(image, (Config.IMAGE_WIDTH, Config.IMAGE_HEIGHT))

    def _get_image(self):
        numpy_surface = np.zeros(self.screen_height*self.screen_width*3, dtype=np.uint8)
        self.ale.getScreenRGB(numpy_surface)
        image = np.reshape(numpy_surface, (self.screen_height, self.screen_width, 3))

        return self._preprocess_image(image)

    def _audio_to_mfcc(self, audio):
        """
            Audio has 512 samples. Considering the audio frequency
            is 30720, frame length of 320 samples (0.010 sec) and
            frame step of 100 samples (0.003 sec) are chosen.
        """
        # Convert to mfcc
        mfcc_data = mfcc(signal=audio, samplerate=Config.AUDIO_FREQ, 
                         winlen=Config.WINLEN, winstep=Config.WINSTEP)
        mfcc_data = np.swapaxes(mfcc_data, 0 ,1)
        
        # Convert to grayscale image and resize
        mfcc_image = imresize(mfcc_data, (Config.IMAGE_WIDTH, Config.IMAGE_HEIGHT), interp='cubic') 
        #  fig = plt.figure(11222)
        #  plt.imshow(mfcc_image)
        #  plt.pause(0.01)

        # Normalization to -1 to 1
        # Ref: https://stats.stackexchange.com/questions/178626/how-to-normalize-data-between-1-and-1
        # TODO I think even this should be normalized more carefully (i.e., with an absolute system rather than relative)
        min_data = np.min(mfcc_image.flatten())
        max_data = np.max(mfcc_image.flatten())
        
        mfcc_image = 1.*(mfcc_image-min_data)/float(max_data-min_data)
        mfcc_image = mfcc_image*2 -1
        
        return mfcc_image

    def _get_image_and_audio(self, pid, count):
        # Get np_data from ale
        np_data_image = np.zeros(self.screen_width*self.screen_height*3, dtype=np.uint8)
        np_data_audio = np.zeros(Config.AUDIO_MAX_DATA, dtype=np.uint8)
        self.ale.get_rgb_audio(np_data_image, np_data_audio)

        while np.count_nonzero(np_data_image) == 0:
            self.ale.act(0)
            self.ale.get_rgb_audio(np_data_image, np_data_audio)

        if np.count_nonzero(np_data_image) == 0:
            raise ValueError("Image data is 0 :(!")
        image = np.reshape(np_data_image, (self.screen_height, self.screen_width, 3)); 
        image = self._preprocess_image(image)
            
        if np.count_nonzero(np_data_audio) == 0:
           print " [DEBUG ] [GameManager.py] audio zero sized!" 
        mfcc = self._audio_to_mfcc(np_data_audio)# To mfcc

        # For debugging audio sync
        if Config.DEBUG_AUDIO_SYNC:
            self._debug_image_audio_sync(image, np_data_audio, pid=pid, count=count)
            
        return image, mfcc

    def _debug_image_audio_sync(self, image, audio, pid=None, count=None):
        if not os.path.exists('debug'):
            os.makedirs('debug')
        if not os.path.exists('debug/'+str(pid)):
            os.makedirs('debug/'+str(pid))

        # Set filename
        filename = str(count); filename = filename.zfill(6);
        filename = 'debug/' + str(pid) + '/' + filename

        # Save image and audio for debugging
        cv2.imwrite(filename+'.png', image)
        write(filename+'.wav', Config.AUDIO_FREQ, audio)

    def reset(self):
        self.ale.reset_game()

        if Config.USE_AUDIO == True:
            self.last_valid_image = None
            self.last_valid_audio = None
            return self._get_image_and_audio(None, None)# Send initial obs to player 
        else:
            return self._get_image()# Send initial obs to player 

    def step(self, action, pid, count):
        # # If no display, sound goes too fast.
        # if Config.DISPLAY_SCREEN == False and Config.USE_AUDIO: 
        #     time.sleep(0.02)# Unit in sec # TODO did extra sleep need to be added here?

        # Action 
        action = self.legal_actions[action]

        # Reward
        reward = self.ale.act(action)

        # Observation
        if Config.USE_AUDIO == True:
            image, audio = self._get_image_and_audio(pid=pid, count=count)
            nextObservation = [image, audio]
        else:
            nextObservation = self._get_image()

        # Game over
        game_over = False
        if self.ale.game_over():
            game_over = True
            self.reset()

        return nextObservation, reward, game_over 
