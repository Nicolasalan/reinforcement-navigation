#!/usr/bin/env python3

# importar bibliotecas comuns
import os
import rospy
import numpy as np
import random
import yaml
import math
import time

# importar mensagens do ROS
from geometry_msgs.msg import Twist, Point, Pose
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
from std_srvs.srv import Empty
from gazebo_msgs.srv import SpawnModel, DeleteModel
from squaternion import Quaternion

# folder to load config file
CONFIG_PATH = "../config/"

# Function to load yaml configuration file
def load_config(config_name):
    with open(os.path.join(CONFIG_PATH, config_name)) as file:
        param = yaml.safe_load(file)

    return param

param = load_config("main_config.yaml")

class Env():
     def __init__(self):

          self.num_scan_ranges = param["num_scan_ranges"]
          self.min_range = param["min_range"]

          self.position = Pose() # posição do robô
          self.goal_position = Pose() # posição do alvo
          self.goal_position.position.x = 0.0 # posição x do alvo
          self.goal_position.position.y = 0.0 # posição y do alvo

          # definir o diretório do robô, alvo e mundo
          self.goal_model_dir = param["target"]

          ##### publicacoes e assinaturas do ROS #####
          self.pub_cmd_vel = rospy.Publisher(param["topic_cmd"], Twist, queue_size=10) # publicar a velocidade do robô
          self.odom = rospy.Subscriber(param["topic_odom"], Odometry, self.odom_callback, queue_size=1) # receber a posição do robô

          ##### servicos do ROS #####
          self.reset_proxy = rospy.ServiceProxy('gazebo/reset_simulation', Empty)
          self.unpause_proxy = rospy.ServiceProxy('gazebo/unpause_physics', Empty)
          self.pause_proxy = rospy.ServiceProxy('gazebo/pause_physics', Empty)
          self.goal = rospy.ServiceProxy('/gazebo/spawn_sdf_model', SpawnModel)
          self.del_model = rospy.ServiceProxy('/gazebo/delete_model', DeleteModel)

          # definir o estado inicial
          self.threshold_target = param["threshold_target"] # distância de chegada

     # funcao para pegar a posicao do robo por meio do topico '/odom' 
     def odom_callback(self, od_data):
          self.last_odom = od_data

     def state(self, scan):
          done = False
          target = False

          # Calculate robot heading from odometry data
          self.odom_x = self.last_odom.pose.pose.position.x
          self.odom_y = self.last_odom.pose.pose.position.y
          quaternion = Quaternion(
               self.last_odom.pose.pose.orientation.w,
               self.last_odom.pose.pose.orientation.x,
               self.last_odom.pose.pose.orientation.y,
               self.last_odom.pose.pose.orientation.z,
          )
          euler = quaternion.to_euler(degrees=False)
          yaw = round(math.degrees(euler[2]))
          angle = round(euler[2], 4) # angulo do robo
          # Calculate distance to the goal from the robot
          distance = np.linalg.norm(
               [self.odom_x - self.goal_position.position.x, self.odom_y - self.goal_position.position.y]
          )
          # Calculate the relative angle between the robots heading and heading toward the goal
          skew_x = self.goal_position.position.x - self.odom_x
          skew_y = self.goal_position.position.y - self.odom_y
          dot = skew_x * 1 + skew_y * 0
          mag1 = math.sqrt(math.pow(skew_x, 2) + math.pow(skew_y, 2))
          mag2 = math.sqrt(math.pow(1, 2) + math.pow(0, 2))
          beta = math.acos(dot / (mag1 * mag2))
          if skew_y < 0:
               if skew_x < 0:
                    beta = -beta
               else:
                    beta = 0 - beta
          theta = beta - angle
          if theta > np.pi:
               theta = np.pi - theta
               theta = -np.pi - theta
          if theta < -np.pi:
               theta = -np.pi - theta
               theta = np.pi - theta
          
          diff = angle - theta
          
          scan_range = self.check_scan_range(scan, scan_range, self.num_scan_ranges)

          if self.min_range > min(scan_range) > 0: # se o robô colidir com algum obstáculo
               done = True
          
          # Detect if the goal has been reached and give a large positive reward
          if distance <= self.threshold_target:
               target = True
               done = True

          return scan_range, distance, theta, diff, yaw, done, target

     def step(self, action):
          target = False

          # Publish the robot action
          vel_cmd = Twist()
          vel_cmd.linear.x = action[0]
          vel_cmd.angular.z = action[1]
          self.pub_cmd_vel.publish(vel_cmd)

          rospy.wait_for_service("/gazebo/unpause_physics")
          try:
               self.unpause()
          except (rospy.ServiceException) as e:
               print("/gazebo/unpause_physics service call failed")

          time.sleep(0.1)

          rospy.wait_for_service("/gazebo/pause_physics")
          try:
               pass
               self.pause()
          except (rospy.ServiceException) as e:
               print("/gazebo/pause_physics service call failed")

          state = np.array([0., 0.])

          data = None
          while data is None:
               try:
                    data = rospy.wait_for_message(param["topic_scan"], LaserScan, timeout=5)
               except:
                    pass

          min_laser, distance, theta, diff, yaw, done, target = self.state(data)
          states = [i / 3.5 for i in min_laser] # normalizar os dados de entrada

          for action in state: # adicionar a ação anterior ao estado
               states.append(action)

          robot_state = [states, distance, theta, diff, yaw, action[0], action[1]]
          reward = self.get_reward(target, done, action, min_laser)

          return np.asarray(robot_state), reward, done

     def reset(self):
          rospy.wait_for_service('/gazebo/delete_model')
          self.del_model('target')

          rospy.wait_for_service('gazebo/reset_simulation')
          try:
               self.reset_proxy()
          except (rospy.ServiceException) as e:
               print("gazebo/reset_simulation service call failed")

          # Build the targetz
          rospy.wait_for_service('/gazebo/spawn_sdf_model')
          try:
               goal_urdf = open(self.goal_model_dir, "r").read()
               target = SpawnModel
               target.model_name = 'target'  # the same with sdf name
               target.model_xml = goal_urdf

               # randomiza o target pelo mundo
               self.goal_position.position.x = random.uniform(-3.6, 3.6)
               self.goal_position.position.y = random.uniform(-3.6, 3.6)
               self.goal(target.model_name, target.model_xml, 'namespace', self.goal_position, 'world')
          except (rospy.ServiceException) as e:
               print("/gazebo/failed to build the target")
          rospy.wait_for_service('/gazebo/unpause_physics')
          
          data = None
          while data is None:
               try:
                    data = rospy.wait_for_message(param["topic_scan"], LaserScan, timeout=5)
               except:
                    pass

          states, distance, theta, diff, yaw, done, target = self.state(data)
          states = [i / 3.5 for i in states]

          states.append(0)
          states.append(0)

          robot_state = [states, distance, theta, diff, yaw]

          return np.asarray(robot_state)

     @staticmethod
     def check_scan_range(scan, scan_range, num_scan_ranges):
          cof = (len(scan.ranges) / (num_scan_ranges - 1)) 
          for i in range(0, num_scan_ranges): 
               n_i = math.ceil(i*cof - 1) 
               if n_i < 0: 
                    n_i = 0 
               if cof == 1:
                    n_i = i 
               if scan.ranges[n_i] == float('Inf'): 
                    scan_range.append(3.5) 
               elif np.isnan(scan.ranges[n_i]): 
                    scan_range.append(0) 
               else:
                    scan_range.append(scan.ranges[n_i]) 
          
          return scan_range

     @staticmethod
     def get_reward(target, done, action, min_laser):
          if target:
               return 100.0
          elif done:
               return -100.0
          else:
               r3 = lambda x: 1 - x if x < 1 else 0.0 # função de recompensa
               return action[0] / 2 - abs(action[1]) / 2 - r3(min_laser) / 2 # acao[0] / 2 mais ação[1] / 2 menos a distância minima do laser / 2
