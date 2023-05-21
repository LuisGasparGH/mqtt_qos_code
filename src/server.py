# Import of all necessary packages and libraries
import paho.mqtt.client as mqtt
import time
import json
import logging
import sys
import threading
import os

# Read the configuration file, which includes information about MQTT topics, various file paths, etc
with open("conf/config.json", "r") as config_file:
    config = json.load(config_file)

# Stores all static variables needed from the read configuration file, as well as the client-id from the input arguments
client_id = str(sys.argv[1])
log_folder = config['logging']['folder']
main_logger = config['logging']['main']
timestamp_logger = config['logging']['timestamp']
broker_address = config['broker_address']
main_topic = config['topics']['main_topic']
begin_client = config['topics']['begin_client']
finish_client = config['topics']['finish_client']
client_done = config['topics']['client_done']
system_runs = config['system_details']['different_runs']
message_details = config['system_details']['message_details']

# Class of the MQTT server code
class MQTT_Server:
    # Configures all loggers which will contain every execution detail for further analysis
    def logger_setup(self):
        # Gathers current time in string format to append to the logger file name, to allow distinction between different runs
        append_time = time.strftime('%T', time.localtime())
        # Sets up the formatter and handlers needed for the loggers
        formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')
        # There are a total of two distinct loggers
        # main_logger - used for normal execution and information and results reporting
        main_log = log_folder + client_id + "-main-" + str(append_time) + ".log"
        main_handler = logging.FileHandler(main_log, mode = 'a')
        main_handler.setFormatter(formatter)
        self.main_logger = logging.getLogger(main_logger)
        self.main_logger.setLevel(logging.DEBUG)
        self.main_logger.addHandler(main_handler)
        # timestamp_logger - used to have an output of all timestamps of messages sent/received
        timestamp_log = log_folder + client_id + "-timestamp-" + str(append_time) + ".log"
        timestamp_handler = logging.FileHandler(timestamp_log, mode = 'a')
        timestamp_handler.setFormatter(formatter)
        self.timestamp_logger = logging.getLogger(timestamp_logger)
        self.timestamp_logger.setLevel(logging.DEBUG)
        self.timestamp_logger.addHandler(timestamp_handler)
        # Console output of the main logger for the user to keep track of the execution status without having to open the logs
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(formatter)
        self.main_logger.addHandler(stdout_handler)

    # Callback for when the client object successfully connects to the broker
    def on_connect(self, client, userdata, flags, rc):
        if rc==0:
            # Upon successful connection, the server subscribes to the client done topic
            # After that, the system handler thread is started
            self.main_logger.info(f"Connected to the broker at {broker_address}")
            self.client.subscribe(client_done, qos=0)
            self.main_logger.info(f"Subscribed to {client_done} topic with QoS 0")
            self.handler_thread.start()
        else:
            # In case of error during connection the log will contain the error code for debugging
            self.main_logger.info(f"Error connecting to broker, with code {rc}")

        # Callback for when the client object successfully disconnects from the broker
    
    # Callback for when the client object successfully disconnects from the broker
    def on_disconnect(self, client, userdata, rc):
        self.main_logger.info(f"Disconnected from broker at {broker_address}")
    
    # Callback for when the server receives a message on the main topic
    # If the topic is the main one, it is just a normal message during that run, and the run counter will increase
    def on_maintopic(self, client, userdata, msg):
        # In order to measure actual publishing frequency, as perceived from the server side, a total runtime of an execution is measured
        if self.run_counter == 0:
            self.run_start_time = time.monotonic()
            self.run_theorical_finish_time = self.run_start_time + self.run_theorical_time
        self.run_finish_time = time.monotonic()
        self.run_counter += 1
        if self.run_finish_time < self.run_theorical_finish_time:
            self.run_intime += 1
        else:
            self.run_late += 1
        self.timestamp_logger.info(f"Received message #{self.run_counter} from the {main_topic} topic (message id: {msg.mid})")
    
    # Callback for when the server receives a message on the client done topic
    # If the topic is the client done topic, it means that one of the clients has finished publishing of his messages
    def on_clientdone(self, client, userdata, msg):
        self.run_clients_done += 1
        # If the total of clients done equals the amount of clients of the run, run is considered finished and packet loss is calculated the logged
        if self.run_clients_done == self.run_client_amount:
            # Once a run is done, packet loss and actual frequency are calculated and logged
            self.run_packet_loss = 100-((self.run_counter/self.run_total_msg_amount)*100)
            self.run_exec_time = self.run_finish_time-self.run_start_time
            self.run_actual_freq = 1/(self.run_exec_time/(self.run_msg_amount-1))
            self.run_time_factor = (self.run_exec_time/self.run_theorical_time)
            self.run_frequency_factor = (self.run_actual_freq/self.run_msg_freq)*100
            self.main_logger.info(f"All {self.run_client_amount} clients finished publishing for this execution")
            self.main_logger.debug(f"RUN RESULTS")
            self.main_logger.info(f"Received {self.run_counter} out of {self.run_total_msg_amount} messages")
            self.main_logger.info(f"Messages received inside time window: {self.run_intime}")
            self.main_logger.info(f"Messages received outside time window: {self.run_late}")
            self.main_logger.info(f"Calculated packet loss: {round(self.run_packet_loss,2)}%")
            self.main_logger.info(f"Run start time (of first received message): {round(self.run_start_time,2)}")
            self.main_logger.info(f"Run theorical finish time: {round(self.run_theorical_finish_time,2)}")
            self.main_logger.info(f"Run actual finish time: {round(self.run_finish_time,2)}")
            self.main_logger.info(f"Total execution time (for {self.run_msg_amount-1} messages): {round(self.run_exec_time,2)} seconds")
            self.main_logger.info(f"Time factor: {round(self.run_time_factor,2)}x of the theorical time")
            self.main_logger.info(f"Actual frequency: {round(self.run_actual_freq,2)} Hz")
            self.main_logger.info(f"Frequency factor: {round(self.run_frequency_factor,2)}%")
            self.client.unsubscribe(main_topic)
            self.run_finished = True
    
    # System handler function, used to feed all clients with each run information and start order. This function is run on a separate thread
    def sys_handler(self):
        # Before the server starts ordering the runs, it will double check the config file to make sure all lists (if existing) have the correct size
        self.wrong_config = False
        for detail in message_details:
            if type(message_details[detail]) == list and len(message_details[detail]) != system_runs:
                self.wrong_config = True
                self.main_logger.warning(f"Problem in config file, {detail} has incorrect number of entries ({len(message_details[detail])}/{system_runs})")
        # In case any issue is found with the config file, performs cleanup and exits
        if self.wrong_config:
            self.cleanup()
        else:
            # The config file has a parameter with the amount of system runs to be performed, which will be iterated in here
            for run in range(system_runs):
                # Prints to the console which run it is, for the user to keep track without having to look at the logs
                self.main_logger.debug(f"PERFORMING RUN {run+1}/{system_runs}...")
                # Resets all needed variables
                self.run_counter = 0
                self.run_intime = 0
                self.run_late = 0
                self.run_clients_done = 0
                # Gathers all the information for the next run to be performed, such as client amount, QoS to be used, message amount, size and publishing frequency
                # This information can be both a list (if it varies over the runs) or an int (if it's the same across all runs)
                if type(message_details['client_amount']) == list:
                    self.run_client_amount = message_details['client_amount'][run]
                else:
                    self.run_client_amount = message_details['client_amount']
                if type(message_details['msg_qos']) == list:
                    self.run_msg_qos = message_details['msg_qos'][run]
                else:
                    self.run_msg_qos = message_details['msg_qos']
                if type(message_details['msg_amount']) == list:
                    self.run_msg_amount = message_details['msg_amount'][run]
                else:
                    self.run_msg_amount = message_details['msg_amount']
                if type(message_details['msg_size']) == list:
                    self.run_msg_size = message_details['msg_size'][run]
                else:
                    self.run_msg_size = message_details['msg_size']
                if type(message_details['msg_freq']) == list:
                    self.run_msg_freq = message_details['msg_freq'][run]
                else:
                    self.run_msg_freq = message_details['msg_freq']
                self.run_total_msg_amount = self.run_msg_amount * self.run_client_amount
                self.run_theorical_time = (self.run_msg_amount-1) / self.run_msg_freq
                # Subscribes to the message topic with the correct QoS to be used in the run, and logs all the information of the run
                self.client.subscribe(main_topic, qos=self.run_msg_qos)
                self.main_logger.debug(f"STARTING NEW RUN")
                self.main_logger.info(f"Subscribed to {main_topic} topic with QoS level {self.run_msg_qos}")
                self.main_logger.info(f"Client amount: {self.run_client_amount} clients")
                self.main_logger.info(f"Message amount per client: {self.run_msg_amount} messages")
                self.main_logger.info(f"Total message amount: {self.run_total_msg_amount} messages")
                self.main_logger.info(f"Message size: {self.run_msg_size} bytes")
                self.main_logger.info(f"Publishing frequency: {self.run_msg_freq} Hz")
                self.main_logger.info(f"QoS level: {self.run_msg_qos}")
                self.main_logger.info(f"Theorical execution time (for {self.run_msg_amount-1} messages): {self.run_theorical_time} seconds")
                # Dumps the information to a JSON payload to send to all the clients, and publishes it to the client topic
                client_config = json.dumps({"client_amount": self.run_client_amount, "msg_qos": self.run_msg_qos, "msg_amount": self.run_msg_amount, "msg_size": self.run_msg_size, "msg_freq": self.run_msg_freq})
                self.client.publish(begin_client, client_config, qos=0)
                self.main_logger.info(f"Sent configuration and start order to all the clients for run #{run+1}")
                self.run_finished = False
                # This is used to wait for the previous run to finish and clean things up before starting a new one
                while self.run_finished == False:
                    pass
            # Once all runs are finished, cleans up everything and exits thread
            self.cleanup()
    
    # Cleanup function, to inform all clients all runs are finished and gracefully closes the connection with the broker
    def cleanup(self):
        self.main_logger.info(f"Performing cleanup of MQTT connection, exiting and informing clients")
        self.client.publish(finish_client, None, qos=0)
        self.client.unsubscribe(main_topic)
        self.client.unsubscribe(client_done)
        self.client.message_callback_remove(main_topic)
        self.client.message_callback_remove(client_done)
        self.client.disconnect()

    # Starts the class with all the variables necessary
    def __init__(self):
        # Creates the logs folder in case it doesn't exist
        os.makedirs(log_folder, exist_ok=True)
        self.logger_setup()
        self.main_logger.debug(f"NEW SYSTEM EXECUTION")
        self.run_finished = True
        # Declares the thread where the system handler will run
        self.handler_thread = threading.Thread(target = self.sys_handler, args=())
        self.main_logger.info(f"Creating MQTT Client with ID {client_id}")
        # Starts the MQTT client with specified ID, passed through the input arguments, and defines all callbacks
        self.client = mqtt.Client(client_id=client_id)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.message_callback_add(main_topic, self.on_maintopic)
        self.client.message_callback_add(client_done, self.on_clientdone)
        # The MQTT client connects to the broker and the network loop iterates forever until the cleanup function
        self.client.connect(broker_address, 1883, 3600)
        self.client.loop_forever()

# Starts one MQTT Server class object
mqtt_server = MQTT_Server()
