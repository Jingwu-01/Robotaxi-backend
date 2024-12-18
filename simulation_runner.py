import threading
import traci
import sumolib
import xml.etree.ElementTree as ET
import random
import sys
from queue import Queue
import time
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import StandardScaler
from contextlib import suppress
import math


class SimulationRunner(threading.Thread):
    def __init__(self, step_length=0.5, sim_start_time=0, sim_end_time=7200, num_people=1000, num_taxis=50, num_chargers=100, optimized=False, output_freq=50):
        """
        Initializes the SimulationRunner object, processes the specified input parameters and defines the global variables

        Args:
        - step_length: interval between each step of the simulation, in seconds
        - sim_start_time: the time of day when the simulation should start, in seconds between 0 (represents midnight) and 7200 (represents midnight of the next day) - note that 300 seconds of simulation time represents 1 hour of real time
        - sim_end_time: similarly, the time of day when the simulation should end, in seconds between 0 and 7200
        - num_people: the number of reservations that will take place over the course of the 7200 second period. these reservations will be distributed throughout the "day" to mimic real-world reservation activity
        - num_taxis: the number of robotic taxis to include in the simulation
        - num_chargers: the number of charging stations to include in the simulation
        - optimized: boolean value that indicates whether the control or the optimized version of the simulation should be run
        - output_freq: how frequently the important data from the simulation should be outputted (in seconds)
        """
        super().__init__()

        # this first group of global variables processes the input parameters
        self.step_length = step_length
        self.sim_start_time = sim_start_time
        self.sim_end_time = sim_end_time
        self.num_people = num_people
        self.num_taxis = num_taxis
        self.num_chargers = num_chargers
        self.optimized = optimized
        self.output_freq = output_freq

        # this second group of global variables describes the configuration of the simulation
        self.network_file = "downtown_houston.net.xml" # the map on which the simulation will run
        self.sumo_cfg = "simulation2.sumocfg" # SUMO's configuration file
        self.net = None # network object created by SUMO after processing the specified map
        self.valid_edges = [] # stores the edges on which it makes sense to initialize a person, taxi, or charger object - SUMO will consider some edges as unreachable, this list will exclude most of those unreachable edges
        self.command_queue = Queue() # stores any dynamic requests made by the user while the simulation is running
        self.stop_event = threading.Event() # stores any stopping requests made by the user while the simulation is running
        self.is_running = False
        self.traci_start_time = -1 # TraCI does not accurately update its time counter from run to run, this variable stores the initial start time TraCI will be using
        self.traci_end_time = -1 # this variable stores the end time TraCI will be using, which is the sum of TraCI's start time and the simulation length

        # this third group of global variables keeps track of people/reservations and each person's current state
        self.person_ids = [] # stores the ids of all the people who will be making reservations during the 7200 second period
        self.all_valid_res = {} # stores the reservation objects. keys are reservation ids, key's value is [person id, from edge, to edge, depart pos, arrival pos, depart time, edges of route from pickup to dropoff, route length]
        self.unreached_reservations = [] # stores the reservation ids that were unreachable in a time step because they were initialized in an inaccessible corner of the map
        self.waiting_reservations = [] # stores reservation ids that have not been assigned to taxis
        self.assigned_reservations = {} # stores reservation ids that have been assigned to taxis and are waiting to be picked up. keys are reservation ids, key's value is taxi id
        self.heading_home_reservations = {} # stores reservation ids that have been picked up by taxis and are on their way to their destinations. keys are reservation ids, key's value is taxi id
        self.completed_reservations = [] # stores the reservation ids that were successfully dropped off
        self.reservation_wait_times = {} # stores the amount of time each reservation had to wait before it was picked up. keys are reservation ids, key's value is difference between reservation's pickup time and depart time

        # this fourth group of global variables keeps track of the taxis and each taxi's current state
        self.taxi_ids = [] # keeps track of all the taxis in the simulation
        self.empty_taxis = {} # keeps track of all the taxis in simulation that are currently unoccupied and unassigned, however these taxis still have random routes. keys are taxi ids, each value is taxi's random destination edge
        self.charging_taxis = {} # keeps track of all the taxis in simulation that are currently on their way to a charger. keys are taxi ids, each value is corresponding charger id
        self.picking_up_taxis = {} # keeps track of all the taxis in simulation that are currently on their way to pick up a person. keys are taxi ids, each value is [reservation id, pickup edge]
        self.dropping_off_taxis = {} # keeps track of all the taxis in simulation that are currently on their way to drop off a person. keys are taxi ids, each value is [reservation id, dropoff edge, pickup time, distance from pickup to dropoff]
        self.out_of_commission = {} # stores the taxis that are inoperable for some reason. keys are taxi ids, each value is [time when taxi can re-enter simulation, amount of charge taxi should be reset with]

        # this fifth group of global variables keeps track of all the values needed to calculate the cost spent by each taxi on charging and towing
        self.cost_per_charging_trip = {} # stores how much each taxi spent on charge everytime it visited a charger. keys are taxi ids, each value is [cost of electricity at first charging trip, cost of electricity at second charging trip, ...]
        self.cost_per_tow = {} # stores how much each taxi spent on charge everytime it needed to be towed due to low charge. keys are taxi ids, each value is [cost of electricity at first tow, cost of electricity at second tow, ...]
        self.tow_base_price = 100 # in $
        self.charge_base_price = round(random.uniform(5,10),1) # in $
        self.electricity_costs = [] # in $/kWh

        # this sixth group of global variables keeps track of all the values needed to calculate the earnings of each taxi from successfully completing reservations
        self.completed_reservations_by_taxi = {} # stores all the reservations each taxi has successfully picked up and dropped off. keys are taxi ids, each value is [distance between pickup and dropoff edges, demand multiplier, time of day rate]
        #   Based on real-world data, GPT suggestions, and the limitations of the simulation, the price of a taxi ride (the amount earned by the taxi company for each ride) will be calculated as follows:
        #       total price = base price + (distance price * demand multiplier * time of day rate), where
        #           base price is some fixed amount, reasonable values are between $4-$8
        #           distance price is the product of a fixed distance rate (price per unit distance) and the distance traveled, reasonable values for distance rate are between $1-$2
        #           demand multiplier is an amplification of the price based on the demand at the time of day
        #           time of day rate is another amplification based on peak hours
        self.taxi_ride_base_price = round(random.uniform(4,8), 1) # in $
        self.taxi_ride_distance_rate = random.randint(10,20)/10 # in $/km
        self.demand_multipliers = []
        self.recent_reservations = [] # keeps track of the number of new reservations that were added in the past six hours, this data is used to calculate demand multiplier
        self.tod_rate = []
        self.tod_rate_normal = round(random.uniform(2,3),2)
        self.tod_rate_morning_rush = self.tod_rate_normal*1.2
        self.tod_rate_evening_rush = self.tod_rate_normal*1.3


        self.electricity_consumption_per_taxi = {} # stores the total amount of electricity used by each taxi since the beginning of the simulation. keys are taxi ids, each value is amount of electricity that taxi has used in Wh
        self.total_distance_driven_per_taxi = {} # stores the total distance each taxi has driven since the beginning of the simulation. keys are taxi ids, each value is amount taxi has driven in km
        
        self.active_chargers = [] # keeps track of all the chargers that are operational

        self.optimized_pending_res_update_time = self.sim_start_time+10 # optimized taxi assignments happen less frequently than in the control in order to minimize redundant driving. this variable keeps track of when to update
        self.all_significant_data_update_time = self.sim_start_time+self.output_freq # keeps track of when to output the significant data from the simulation

        # Counters
        self.person_counter = 0
        self.new_res_counter = 0
        self.taxi_counter = 0
        self.extra_route_counter = 0
        self.charger_counter = 0

    def run(self):
        """
        The main execution of the simulation
        """
        # start = time.time()
        if self.step_length <= 0 or self.num_people < 0 or self.num_taxis < 0 or self.num_chargers < 0 or self.output_freq < 0:
            print("Please specify non-negative values for all input numbers")
            sys.exit(1)
        if self.sim_start_time < 0 or self.sim_end_time > 7200 or self.sim_end_time < self.sim_start_time:
            print("Please specify a start time that is a positive value and an end time that is not greater than 7200,\nand please make sure the start time is not greater than the end time\n")
            sys.exit(1)
        if self.output_freq < self.step_length:
            print("Please specify an output frequency that is greater than the step length")
            sys.exit(1)
        self.is_running = True
        try:
            self.initialize_network()
            self.initialize_simulation()
            self.traci_start_time = traci.simulation.getTime()
            self.traci_end_time = self.sim_end_time - self.sim_start_time + self.traci_start_time
            self.generate_detectors_xml()
            self.generate_persons_xml()
            self.write_times_into_sumo_file()
            self.spawn_taxis()
            self.simulation_loop()
        except Exception as e:
            print(f"Error during simulation: {e}")
        finally:
            self.cleanup()
        # end = time.time()
        # print(f"It took {end-start} seconds ({(end-start)/60} minutes) to run this program")

    def initialize_network(self):
        """
        Initializes the network by processing the specified map and storing important information
        """
        print("Initializing network and filtering valid edges...")
        self.net = sumolib.net.readNet(self.network_file)
        self.valid_edges = [
            edge.getID()
            for edge in self.net.getEdges()
            if edge.getLaneNumber() > 0 and edge.getOutgoing() and edge.getIncoming() and edge.getLanes()[0].getLength() >= 30
        ] # stores the edges in the simulation that are less likely to be unreachable
        print(f"Num valid edges: {len(self.valid_edges)}")


    def initialize_simulation(self):
        """
        Initializes the SUMO simulation and activates TraCI
        """
        print("Starting SUMO simulation...")
        sumo_binary = sumolib.checkBinary('sumo-gui')
        sumo_cmd = [
            sumo_binary,
            "-c",
            self.sumo_cfg,
            "--start",
            "--quit-on-end",
            "--step-length",
            str(self.step_length),
            "--additional-files",
            "vehicle_type.add.xml,persons.add.xml,detectors.add.xml",
            "--collision.action",
            "none",
        ]
        traci.start(sumo_cmd)
        print("SUMO simulation started.")

    def generate_detectors_xml(self):
        """
        Adds the user-specified number of chargers at random locations
        """
        detectors = []
        for _ in range(self.num_chargers):
            edge_id = random.choice(self.valid_edges)
            lane = self.net.getEdge(edge_id).getLanes()[0]
            lane_length = lane.getLength()
            # safest to initialize person and charger objects in the middle of their specified lanes to prevent taxis from disappearing from the simulation when they reach their destination
            lane_pos = random.uniform(max(lane_length*(1/4), 13), min(lane_length*(3/4), lane_length-13))
            charger_id = f"charger_{self.charger_counter}"
            self.charger_counter += 1
            self.active_chargers.append((charger_id, lane.getID(), lane_pos))
            detectors.append(f'''
<inductionLoop id="{charger_id}" lane="{lane.getID()}" pos="{str(lane_pos)}" freq="10" file="detector_output.xml" />
            ''')
            # print(f"Added valid charger {charger_id} on lane {lane.getID()} (length: {lane_length}) at position {lane_pos}")
        with open('detectors.add.xml', 'w') as f:
            f.write('<additional>\n')
            f.writelines(detectors)
            f.write('</additional>\n')
        print(f"Updated detectors.add.xml with {len(detectors)} chargers.")

    def generate_persons_xml(self):
        """
        Creates the user-specified number of people, assigns each a departure time, and chooses pickup and dropoff locations at random but ensures a route exists between them
        """
        persons = []
        for _ in range(self.num_people):
            edges_are_valid = False
            pickup_edge_id = random.choice(self.valid_edges)
            dropoff_edge_id = random.choice(self.valid_edges)
            curr_route = None
            while not edges_are_valid:
                curr_route = traci.simulation.findRoute(pickup_edge_id, dropoff_edge_id, vType="car")
                if curr_route and curr_route.edges and dropoff_edge_id != pickup_edge_id:
                    edges_are_valid = True
                else:
                    pickup_edge_id = random.choice(self.valid_edges)
                    dropoff_edge_id = random.choice(self.valid_edges)
            person_id = f"person_{self.person_counter}"
            self.person_counter += 1
            self.person_ids.append(person_id)
            depart_time = self.set_depart_time()
            pickup_lane = self.net.getEdge(pickup_edge_id).getLanes()[0]
            dropoff_lane = self.net.getEdge(dropoff_edge_id).getLanes()[0]
            persons.append(f'''
<person id="{person_id}" depart="{depart_time}">
    <stop lane="{pickup_lane.getID()}" duration = "{7200 - depart_time}"/>
</person>
            ''') # duration ensures that a person can stay in the simulation for as long as it takes to get picked up by a taxi
            pickup_pos = random.uniform(max(pickup_lane.getLength()*(1/4), 13), min(pickup_lane.getLength()*(3/4), pickup_lane.getLength()-13))
            dropoff_pos = random.uniform(max(dropoff_lane.getLength()*(1/4), 13), min(dropoff_lane.getLength()*(3/4), dropoff_lane.getLength()-13))
            res_id = len(persons)-1
            self.all_valid_res[res_id] = [person_id, pickup_edge_id, dropoff_edge_id, pickup_pos, dropoff_pos, depart_time, curr_route.edges, curr_route.length]
            # print(f"Person {person_id} added with ride from {pickup_edge_id} to {dropoff_edge_id}")
        with open('persons.add.xml', 'w') as f:
            f.write('<additional>\n')
            f.writelines(persons)
            f.write('</additional>\n')
        print(f"Validated and wrote {len(persons)} person routes to 'persons.add.xml'.")

    def set_depart_time(self):
        """
        Decides a passenger's depart time based on real-world reservation activity trends. If the depart time is set to be earlier than the user-specified start time,
        which can happen if the user starts the simulation in the middle of the day, the person will depart at the simulation's start time

        Returns:
        - the simulation time in seconds at which the passenger should depart
        """
        depart_time_prob = random.random()
        if depart_time_prob <= 0.06:  # 6% of reservations should happen between midnight and 6am
            depart_time = round(random.uniform(0, 1800), 1)
        elif depart_time_prob <= 0.13:  # 7% of reservations should happen between 6am and 8am
            depart_time = round(random.uniform(1800, 2400), 1)
        elif depart_time_prob <= 0.24:  # 11% of reservations should happen between 8am and 10am
            depart_time = round(random.uniform(2400, 3000), 1)
        elif depart_time_prob <= 0.5:  # 26% of reservations should happen between 10am and 2pm
            depart_time = round(random.uniform(3000, 4200), 1)
        elif depart_time_prob <= 0.61:  # 11% of reservations should happen between 2pm and 4pm
            depart_time = round(random.uniform(4200, 4800), 1)
        elif depart_time_prob <= 0.7:  # 9% of reservations should happen between 4pm and 6pm
            depart_time = round(random.uniform(4800, 5400), 1)
        elif depart_time_prob <= 0.83:  # 13% of reservations should happen between 6pm and 8pm
            depart_time = round(random.uniform(5400, 6000), 1)
        elif depart_time_prob <= 0.94:  # 11% of reservations should happen between 8pm and 10pm
            depart_time = round(random.uniform(6000, 6600), 1)
        else:  # 6% of reservations should happen between 10pm and 11:59pm
            depart_time = round(random.uniform(6600, 7200), 1)

        return max(depart_time, self.sim_start_time)
    
    def write_times_into_sumo_file(self):
        """
        Adds the user-specified start and end times to the simulation's configuration file
        """
        tree = ET.parse(self.sumo_cfg)
        root = tree.getroot()
        for time in root.iter("time"):
            begin_element = time.find("begin")
            if begin_element is not None:
                begin_element.set("value", str(self.sim_start_time))
            end_element = time.find("end")
            if end_element is not None:
                end_element.set("value", str(self.sim_end_time))
        tree.write(self.sumo_cfg)

    def spawn_taxis(self):
        """
        Creates the user-specified number of taxis and initializes each with a random route and a random amount of charge
        """
        for _ in range(self.num_taxis):
            edges_are_valid = False
            rand_route = None
            start_edge_id = random.choice(self.valid_edges)
            dest_edge_id = random.choice(self.valid_edges)
            while not edges_are_valid:
                rand_route = traci.simulation.findRoute(start_edge_id, dest_edge_id, vType="car")
                if rand_route and rand_route.edges and dest_edge_id != start_edge_id:
                    edges_are_valid = True
                else:
                    start_edge_id = random.choice(self.valid_edges)
                    dest_edge_id = random.choice(self.valid_edges)
            taxi_id = f"taxi_{self.taxi_counter}"
            self.taxi_counter += 1
            route_id = f"route_{taxi_id}"
            traci.route.add(route_id, rand_route.edges)
            traci.vehicle.add(taxi_id, routeID=route_id, typeID="car", departPos="random", departLane="best", departSpeed="max")
            self.empty_taxis[taxi_id] = dest_edge_id
            charge_amount = random.randint(7,60)
            charge_amount = charge_amount*100
            traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", charge_amount) #Wh
            traci.vehicle.setParameter(taxi_id, "device.battery.maximumBatteryCapacity", 8000)  # Wh
            # print(f"Spawned {taxi_id} at edge {start_edge_id} with {charge_amount} Wh charge (maximum is {float(traci.vehicle.getParameter(taxi_id, 'device.battery.maximumBatteryCapacity'))} Wh)")
            self.taxi_ids.append(taxi_id)
        print(f"Confirmed Taxis in Simulation: {len(self.taxi_ids)}")


    def get_status(self):
        """
        Fetch the current simulation status.
        
        Returns
        - the simulation status, including the current time and the numbers of active people, taxis, and chargers
        """
        try:
            simulation_time = traci.simulation.getTime() - self.traci_start_time
            # num_taxis = len([tid for tid in traci.vehicle.getIDList() if tid in self.taxi_ids])
            # num_people = len([pid for pid in traci.person.getIDList() if pid in self.person_ids])
            num_taxis_in_sim = len(traci.vehicle.getIDList())
            num_taxis_out_of_commission = len(self.out_of_commission.keys())
            num_people_in_sim = len(self.waiting_reservations) + len(self.assigned_reservations.keys()) + len(self.heading_home_reservations.keys()) # both number of pending reservations and number of occupied taxis
            # Please note that num_people_in_sim will be different from the number of people shown on the bottom right corner of the SUMO display window. This is because,
            # due to our implementation, the number on the SUMO window only counts the number of reservations waiting to be picked up, and omits the number of people
            # riding in taxis
            num_active_chargers = len(self.active_chargers)

            # return {
            #     "simulation_time": simulation_time,
            #     "num_taxis": num_taxis,
            #     "num_people": num_people,
            #     "num_chargers": num_chargers,
            # }
            return {
                "simulation_time": simulation_time,
                "num_taxis_in_sim": num_taxis_in_sim,
                "num_taxis_out_of_commission": num_taxis_out_of_commission,
                "num_people_in_sim": num_people_in_sim,
                "num_active_chargers": num_active_chargers,
            }
        except Exception as e:
            print(f"Error fetching simulation status: {e}")
            return {}

    def simulation_loop(self):
        """
        Runs the simulation from the user-specified start time to the user-specified end time, monitors the states of people/reservations, taxis, and chargers
        """
        print("Simulation loop started.")
        simulation_time = self.sim_start_time

        
        # The optimized version builds a predictive model to guess future electricity prices based on provided historical data
        if self.optimized:
            path_to_data = "historical_elec_cost_data.xlsx"
            historical_data = self.load_historical_data(path_to_data)
            x_time, y_price = self.get_hist_data(historical_data)
            pred_models = self.train_prediction_models(x_time, y_price)
        
        while not self.stop_event.is_set() and simulation_time < self.sim_end_time:
            if simulation_time >= self.all_significant_data_update_time or simulation_time==self.sim_start_time:
                print(f"Time: {simulation_time}")

            # Process commands from the queue
            while not self.command_queue.empty():
                command = self.command_queue.get()
                try:
                    action = command.get("action")
                    if action == "add_person":
                        self._add_people(command["num_people"])
                    elif action == "remove_person":
                        self._remove_people(command["num_people"])
                    elif action == "add_taxi":
                        self._spawn_taxis_at_runtime(command["num_taxis"])
                    elif action == "remove_taxi":
                        self._remove_taxis(command["num_taxis"])
                    elif action == "add_charger":
                        self._add_chargers_at_runtime(command["num_chargers"])
                    elif action == "remove_charger":
                        self._remove_chargers(command["num_chargers"])
                except Exception as e:
                    print(f"Error processing command {command}: {e}")

            try:
                # Step the simulation forward
                traci.simulationStep()

                # TraCI does not accurately update its taxi ID list from run to run, this forces a clean slate by deleting any people that carried over from the previous run
                if simulation_time == self.sim_start_time:
                    self.clear_residual_variables()

                for unreached_res_id in self.unreached_reservations:
                    # We get here if a reservation that was set at runtime is actually unreachable by the taxis
                    # (Since they are placed at random locations, sometimes this happens)
                    # So at this point in the program, the reservation is reinitialized at a new random location
                    unreached_person_id = self.all_valid_res[unreached_res_id][0]
                    del self.all_valid_res[unreached_res_id]
                    traci.person.remove(unreached_person_id)
                    self.reinit_res(unreached_res_id, unreached_person_id, simulation_time)
                    print(f"Reservation #{unreached_res_id} was unreached last time step and has now been reinitialized")
                self.unreached_reservations.clear()
                
                # Checks the reservations for any new ones that should be added to the sim (if their depart time has just passed) and creates them
                for res_id in self.all_valid_res.keys():
                    end_cond = self.all_valid_res[res_id][5]<=simulation_time and res_id not in self.unreached_reservations and res_id not in self.waiting_reservations and res_id not in self.assigned_reservations.keys() and res_id not in self.heading_home_reservations.keys() and res_id not in self.completed_reservations
                    if end_cond:
                        #print(f"Reservation #{res_id} has just departed")
                        self.new_res_counter += 1
                        self.waiting_reservations.append(res_id)
                        traci_depart_time = self.all_valid_res[res_id][5]-self.sim_start_time+self.traci_start_time # because TraCI does not accurately update its timekeeping from run to run, this scales the simulation depart time to the equivalent time when TraCI should add it
                        traci.person.add(self.all_valid_res[res_id][0], edgeID=self.all_valid_res[res_id][1], pos=self.all_valid_res[res_id][3], depart=traci_depart_time)
                        traci.person.appendWaitingStage(self.all_valid_res[res_id][0], duration=max(0, self.traci_end_time - traci_depart_time))
                        traci.person.setColor(self.all_valid_res[res_id][0], (135,0,175)) # in simulation, people are purple triangles
                        traci.person.setWidth(self.all_valid_res[res_id][0], 3)
                        traci.person.setLength(self.all_valid_res[res_id][0], 3)
                # print(f"New reservations added: {self.new_res_counter}")
                # print(f"Updated Waiting Reservations: {self.waiting_reservations}")

                # Gets the numbers of active people and taxis in the simulation, periodically outputs information about the states of people, taxis, and chargers
                taxis_in_sim = traci.vehicle.getIDList()
                if simulation_time >= self.all_significant_data_update_time or simulation_time==self.sim_start_time:
                    print(f"Total number of pending reservations in sim: {len(self.waiting_reservations) + len(self.assigned_reservations.keys())}") # the number of reservations that have been initialized (excludes reservations with depart times in the future) but have not been picked up by a taxi
                    print(f"Number of unassigned reservations: {len(self.waiting_reservations)}")
                    print(f"Number of assigned reservations: {len(self.assigned_reservations.keys())}")
                    print(f"Number of reservations that are currently inside taxis: {len(self.heading_home_reservations.keys())}")
                    print(f"Number of completed reservations: {len(self.completed_reservations)}")
                    print(f"Number of active chargers: {len(self.active_chargers)}")
                    print(f"Number of active taxis in sim: {len(taxis_in_sim)}") # excludes taxis that have been put out of commission
                    print(f"Number of taxis that are out of commission: {len(self.out_of_commission.keys())}")
                    print(f"Number of total taxis: {len(self.taxi_ids)}") # sum of active taxis and taxis that are out of commission
                    print(f"Number of unoccupied, unassigned taxis: {len(self.empty_taxis.keys())}")
                    print(f"Number of unoccupied taxis that have been assigned to reservations: {len(self.picking_up_taxis.keys())}")
                    print(f"Number of unoccupied taxis that are on their way to chargers: {len(self.charging_taxis)}")
                    print(f"Number of occupied taxis: {len(self.dropping_off_taxis.keys())}")

                # Some of the variables needed to calculate costs and earnings depend on the simulation time, this sets those variables
                self.set_time_dependent_price_variables(simulation_time)

                # Sometimes spawning a taxi will fail. this redoes the spawning for the missed taxi
                # Sometimes though, resetting just doesn't work, and the taxi isn't put back into the simulation
                # This necessitates a check in all further sections of the code, where a taxi is only considered if it is in the simulation
                if len(taxis_in_sim) + len(self.out_of_commission.keys()) < len(self.taxi_ids):
                    for taxi_id in self.taxi_ids:
                        if taxi_id not in taxis_in_sim and taxi_id not in self.out_of_commission.keys():
                            with suppress(Exception):
                                traci.vehicle.remove(taxi_id)  # sometimes the car does actually exist but for some reason TraCI can't retrieve it. this removes it so it can be reset
                            new_battery_level = random.randint(7, 60)
                            self.reset_taxi_loc(taxi_id, new_battery_level * 100)
                            taxis_in_sim = traci.vehicle.getIDList()

                back_in_commission = []
                for taxi_id in self.out_of_commission.keys():
                    # We might get here if a taxi was initialized in a corner of the map where it can't reach any reservations (since they are placed at random locations, sometimes this happens),
                    # if a taxi ran out of battery and needed to be towed, or if it wound up on an unreachable edge while randomly circling. The taxi is treated as out of commission for a certain
                    # amount of time (depending on the reason), then when the time has passed, put the taxi back into the simulation at a new location
                    if self.out_of_commission[taxi_id][0] <= simulation_time:
                        self.reset_taxi_loc(taxi_id, self.out_of_commission[taxi_id][1])
                        back_in_commission.append(taxi_id)
                        print(f"Out of commission taxi #{taxi_id} has been put back into the simulation at a new location")
                for taxi_id in back_in_commission:
                    del self.out_of_commission[taxi_id]

                # The main purpose of this block of code is to check if a taxi has run out of battery, and deal with this accordingly based on the taxi's state, including calculating the cost of the resulting tow
                # Also takes advantage of the iteration through every taxi to update the electricity consumption and total driving data
                for taxi_id in self.taxi_ids:
                    if taxi_id in traci.vehicle.getIDList():
                        if taxi_id in self.electricity_consumption_per_taxi.keys():
                            self.electricity_consumption_per_taxi[taxi_id] += traci.vehicle.getElectricityConsumption(taxi_id)*self.step_length
                        else:
                            self.electricity_consumption_per_taxi[taxi_id] = traci.vehicle.getElectricityConsumption(taxi_id)*self.step_length
                        self.total_distance_driven_per_taxi[taxi_id] = traci.vehicle.getDistance(taxi_id)/1000 # in km
                        if float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity")) <= 200:
                            traci.vehicle.setColor(taxi_id, (255,0,0)) # taxis turn red when they get really low on battery
                        if taxi_id not in self.out_of_commission.keys() and float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity")) <= 25:
                            print(f"OH NO! TAXI {taxi_id} RAN OUT OF BATTERY")
                            if taxi_id in self.dropping_off_taxis.keys():
                                curr_edge = traci.vehicle.getRoadID(taxi_id)
                                curr_pos = traci.vehicle.getLanePosition(taxi_id)
                                curr_res_id = self.dropping_off_taxis[taxi_id][0]
                                print(f"\tTaxi {taxi_id} was on its way to drop off a passenger at reservation #{curr_res_id}")
                                self.reset_res(curr_res_id, curr_edge, curr_pos, simulation_time)
                                del self.heading_home_reservations[curr_res_id]
                                print(f"\tReservation #{curr_res_id} has been unassigned")
                                del self.dropping_off_taxis[taxi_id]
                            elif taxi_id in self.picking_up_taxis.keys():
                                curr_res_id = self.picking_up_taxis[taxi_id][0]
                                print(f"\tTaxi {taxi_id} was on its way to pick up a passenger at reservation #{curr_res_id}")
                                self.waiting_reservations.append(curr_res_id)
                                del self.assigned_reservations[curr_res_id]
                                print(f"\tReservation #{curr_res_id} has been unassigned")
                                del self.picking_up_taxis[taxi_id]
                            else:
                                if taxi_id in self.charging_taxis.keys():
                                    print(f"\tTaxi {taxi_id} was on its way to charge")
                                    del self.charging_taxis[taxi_id]
                                else:
                                    print(f"\tTaxi {taxi_id} was unassigned")
                                    del self.empty_taxis[taxi_id]
                            self.out_of_commission[taxi_id] = [simulation_time + 300, 8000]
                            charge_added = 8000-float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity")) # in Wh
                            charge_added = charge_added/1000 # in kWh
                            price_of_charge = charge_added * self.electricity_costs[-1] # in $
                            if taxi_id in self.cost_per_tow.keys():
                                self.cost_per_tow[taxi_id].append(price_of_charge)
                            else:
                                self.cost_per_tow[taxi_id] = [price_of_charge]
                            traci.vehicle.remove(taxi_id)
                            print(f"\tTaxi {taxi_id} has been put out of commission and is no longer in sim")


                # This block of code performs taxi assignment. If a taxi is running low on battery, it is sent to a charger (optimized version also considers prices), otherwise it can be sent to a pending reservation
                new_charging_assignments = {} # stores which taxis will start to go to which charger this time step. keys are taxi ids, each value is [charger id, distance to charger, route to charger]
                new_reservation_assignments = {} # stores which taxis will start to go to pick up which person this time step. keys are taxi ids, each value is [reservation id, distance to pickup point, route to pickup point]
                if not self.optimized or simulation_time>=self.optimized_pending_res_update_time: # optimized version performs assignments less frequently in order to let the list of pending reservations build up more. allows taxi assignment to mimimize redundant driving
                    to_charger = []  # stores which taxis of the available ones need to head to charger this time step
                    to_reservation = [] # stores which taxis of the available ones are heading to some reservation's pickup point this time step
                    for taxi_id in self.empty_taxis.keys():
                        if taxi_id in traci.vehicle.getIDList():
                            battery_level = float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity"))
                            if not self.optimized: # control will charge if battery is below some amount. this amount varies at each iteration to mimic how the average human will randomly decide to refuel when the current gas/battery gets down to some range
                                if battery_level < (random.randint(50,60)*10):
                                    to_charger.append(taxi_id)
                                    traci.vehicle.setColor(taxi_id, (255,165,0)) # taxis turn orange when they reach low charge
                                else:
                                    to_reservation.append(taxi_id)
                            else: # optimized uses a set threshold that doesn't vary (more closely mimicing how a robot fleet might make decisions) to consider charging. makes charging decision based on predicted future electricity prices
                                low_battery_threshold = 3000  # Wh - if battery is below this amount, might charge
                                charging_decision = False
                                if battery_level < low_battery_threshold:
                                    charging_decision = self.optimized_charging(taxi_id, pred_models, simulation_time, battery_level)
                                if charging_decision:
                                    to_charger.append(taxi_id)
                                else:
                                    to_reservation.append(taxi_id)
                    if self.optimized:
                        self.optimized_pending_res_update_time += 10
                    if len(to_charger) > 0: # assigns taxis that need to charge to their closest charger
                        active_chargers_copy = self.active_chargers[:]
                        new_charging_assignments = self.find_nearest_charger(active_chargers_copy, to_charger)
                    if len(self.waiting_reservations) > 0 and len(to_reservation) > 0: # assigns taxis to reachable reservations
                        waiting_res_copy = self.waiting_reservations[:]
                        if not self.optimized:
                            new_reservation_assignments = self.efficient_taxi_assignment(waiting_res_copy, to_reservation, simulation_time)
                        else: # optimized version reduced redundant driving
                            new_reservation_assignments = self.optimized_taxi_assignment(waiting_res_copy, to_reservation, simulation_time)

                # For any taxis that need to charge, uses the computed assignments to send them to chargers
                for taxi_id in new_charging_assignments.keys():
                    if taxi_id in traci.vehicle.getIDList():
                        try:
                            traci.vehicle.setRoute(taxi_id, new_charging_assignments[taxi_id][2].edges)
                            del self.empty_taxis[taxi_id]
                            self.charging_taxis[taxi_id] = new_charging_assignments[taxi_id][0]
                        except:
                            curr_edge = traci.vehicle.getRoadID(taxi_id)
                            for charger_info in self.active_chargers:
                                if (charger_info[0]==new_charging_assignments[taxi_id][0]):
                                    curr_charger_lane = charger_info[1]
                                    charger_edge = traci.lane.getEdgeID(curr_charger_lane)
                                    route_to_charger = traci.simulation.findRoute(curr_edge, charger_edge, vType="car")
                                    traci.vehicle.setRoute(taxi_id, route_to_charger.edges)
                                    del self.empty_taxis[taxi_id]
                                    self.charging_taxis[taxi_id] = new_charging_assignments[taxi_id][0]
                                    break
                        # print(f"Taxi {taxi_id} is on its way to charger {self.charging_taxis[taxi_id]} and is no longer unassigned")

                # Checks taxis that have been sent to chargers and monitors if they reach those chargers. Charges taxi to full and treats it as unoccupied. Keeps track of the cost of charging
                delete_from_charging_taxis = []
                for taxi_id in self.charging_taxis.keys():
                    if taxi_id in traci.vehicle.getIDList():
                        corr_charger_id = self.charging_taxis[taxi_id]
                        curr_taxi_edge = traci.vehicle.getRoadID(taxi_id)
                        for charger_info in self.active_chargers:
                            if charger_info[0] == corr_charger_id:
                                curr_charger_lane = charger_info[1]
                                curr_charger_edge = traci.lane.getEdgeID(curr_charger_lane)
                                if curr_taxi_edge == curr_charger_edge:
                                    charger_pos = charger_info[2]
                                    if traci.vehicle.getLanePosition(taxi_id) >= charger_pos:
                                        # print(f"{taxi_id} successfully reached charger {corr_charger_id}")
                                        delete_from_charging_taxis.append(taxi_id)
                                        init_bat = float(traci.vehicle.getParameter(taxi_id, 'device.battery.actualBatteryCapacity'))
                                        # print(f"\tTaxi {taxi_id} reached charger with {init_bat} Wh remaining")
                                        self.empty_taxis[taxi_id] = traci.vehicle.getRoadID(taxi_id)
                                        traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", 8000)  # Wh
                                        traci.vehicle.setColor(taxi_id, (0,255,0)) # turns green again when it's fully charged
                                        curr_bat = float(traci.vehicle.getParameter(taxi_id, 'device.battery.actualBatteryCapacity'))
                                        # print(f"\t\tTaxi {taxi_id} reached charger {corr_charger_id} and is now charged to {curr_bat}")
                                        charge_added = (curr_bat-init_bat)/1000 # in kWh
                                        price_of_charge = charge_added * self.electricity_costs[-1]
                                        if taxi_id in self.cost_per_charging_trip.keys():
                                            self.cost_per_charging_trip[taxi_id].append(price_of_charge)
                                        else:
                                            self.cost_per_charging_trip[taxi_id] = [price_of_charge]
                                break
                for taxi_id in delete_from_charging_taxis:
                    del self.charging_taxis[taxi_id]

                # For any taxis that can be sent to a pending reservation, uses the computed assignments to send them to those reservations
                for taxi_id in new_reservation_assignments.keys():
                    if taxi_id in traci.vehicle.getIDList():
                        res_id = new_reservation_assignments[taxi_id][0]
                        try:
                            traci.vehicle.setRoute(taxi_id, new_reservation_assignments[taxi_id][2].edges)
                        except:
                            curr_edge = traci.vehicle.getRoadID(taxi_id)
                            res_pickup_edge = self.all_valid_res[res_id][1]
                            route_to_pickup = traci.simulation.findRoute(curr_edge, res_pickup_edge, vType="car")
                            traci.vehicle.setRoute(taxi_id, route_to_pickup.edges)
                        self.waiting_reservations.remove(res_id)
                        self.assigned_reservations[res_id] = taxi_id
                        # print(f"Reservation #{res_id} was assigned to taxi {taxi_id}, so is no longer unassigned")
                        del self.empty_taxis[taxi_id]
                        self.picking_up_taxis[taxi_id] = [res_id, self.all_valid_res[res_id][1]]
                        # print(f"Taxi {taxi_id} is on its way to pick up person at reservation #{self.picking_up_taxis[taxi_id][0]} and is no longer unassigned")

                # Checks taxis that have been sent to pick up reservations and monitors if they reach those people. Person boards taxi, taxi is treated as occupied. Keeps track of the reservation's wait time
                for taxi_id in self.picking_up_taxis.keys():
                    if taxi_id in traci.vehicle.getIDList():
                        curr_edge = traci.vehicle.getRoadID(taxi_id)
                        if curr_edge == self.picking_up_taxis[taxi_id][1]:
                            curr_res_id = self.picking_up_taxis[taxi_id][0]
                            if traci.vehicle.getLanePosition(taxi_id) >= self.all_valid_res[curr_res_id][3]:
                                # print(f"{taxi_id} successfully picked up passenger at reservation #{curr_res_id}")
                                traci.vehicle.setColor(taxi_id, (65,225,200)) # occupied taxis are blue
                                traci.person.remove(self.all_valid_res[curr_res_id][0])
                                del self.assigned_reservations[curr_res_id]
                                self.heading_home_reservations[curr_res_id] = taxi_id
                                # print(f"Reservation #{curr_res_id} was picked up by taxi {taxi_id}, so is no longer waiting for pickup")
                                self.dropping_off_taxis[taxi_id] = [curr_res_id, self.all_valid_res[curr_res_id][2], simulation_time, 0]
                                # print(f"Taxi {taxi_id} is on its way to dropoff person at reservation #{self.dropping_off_taxis[taxi_id][0]}")
                                if curr_res_id in self.reservation_wait_times.keys():
                                    self.reservation_wait_times[curr_res_id] += simulation_time - self.all_valid_res[curr_res_id][5]
                                    # print(f"This reservation's depart time was {all_valid_res[curr_res_id][5]} and pickup time was {simulation_time}")
                                else:
                                    self.reservation_wait_times[curr_res_id] = simulation_time - self.all_valid_res[curr_res_id][5]
                                    # print(f"This reservation's depart time was {all_valid_res[curr_res_id][5]} and pickup time was {simulation_time}")

                # Checks occupied taxis and monitors if they reach person's dropoff point. Taxi is treated as unoccupied. Keeps track of the completed reservation
                for taxi_id in self.dropping_off_taxis.keys():
                    if taxi_id in traci.vehicle.getIDList():
                        curr_edge = traci.vehicle.getRoadID(taxi_id)
                        curr_res_id = self.dropping_off_taxis[taxi_id][0]
                        if taxi_id in self.picking_up_taxis.keys():
                            del self.picking_up_taxis[taxi_id]
                            # print(f"Taxi {taxi_id} is no longer picking up")
                            route_edges_to_dropoff = self.all_valid_res[curr_res_id][6]
                            try:
                                traci.vehicle.setRoute(taxi_id, route_edges_to_dropoff)
                            except:
                                route_to_dropoff = traci.simulation.findRoute(curr_edge, self.all_valid_res[curr_res_id][2], vType="car")
                                traci.vehicle.setRoute(taxi_id, route_to_dropoff.edges)
                            # print(f"Taxi {taxi_id} has a new route from {curr_edge} to {self.dropping_off_taxis[taxi_id][1]}")
                            self.dropping_off_taxis[taxi_id][3] = self.all_valid_res[curr_res_id][7]
                        if curr_edge == self.dropping_off_taxis[taxi_id][1]:
                            # print(f"{taxi_id} is at destination edge {curr_edge} (dropping off), position {traci.vehicle.getLanePosition(taxi_id)}")
                            # print(f"\tpassenger wants to go to position {self.all_valid_res[curr_res_id][4]}")
                            if self.all_valid_res[curr_res_id][4] <= traci.vehicle.getLanePosition(taxi_id):
                                # print(f"{taxi_id} successfully dropped off passenger at reservation #{curr_res_id}")
                                traci.vehicle.setColor(taxi_id, (0,255,0)) # taxi turns green again when it's empty
                                del self.heading_home_reservations[curr_res_id]
                                self.completed_reservations.append(curr_res_id)
                                # print(f"Reservation #{curr_res_id} was dropped off by taxi {taxi_id}, so is no longer picked up")
                                self.empty_taxis[taxi_id] = traci.vehicle.getRoadID(taxi_id)
                                # print(f"Taxi {taxi_id} has just dropped off person at reservation #{curr_res_id}")
                                if taxi_id in self.completed_reservations_by_taxi.keys():
                                    self.completed_reservations_by_taxi[taxi_id].append([self.dropping_off_taxis[taxi_id][3], self.demand_multipliers[-1], self.tod_rate[-1]])
                                else:
                                    self.completed_reservations_by_taxi[taxi_id] = [[self.dropping_off_taxis[taxi_id][3], self.demand_multipliers[-1], self.tod_rate[-1]]]


                # Unoccupied, unassigned taxis randomly circle the map until they get assigned. This code block monitors these taxis and assigns them new random routes if they complete their old ones
                # Occasionally, random circling will cause a taxi to end up on an unreachable edge, in this case it is briefly taken out of commission
                delete_from_empty_taxis = []
                for taxi_id in self.empty_taxis.keys():
                    if taxi_id in traci.vehicle.getIDList():
                        #print(f"{taxi_id}: {traci.vehicle.getRoadID(taxi_id)}")
                        if taxi_id in self.dropping_off_taxis.keys():
                            del self.dropping_off_taxis[taxi_id]
                            # print(f"Taxi {taxi_id} is no longer dropping off")
                        if self.empty_taxis[taxi_id] == traci.vehicle.getRoadID(taxi_id):
                            # print(f"{taxi_id} has reached its destination edge")
                            valid_edges_copy = self.valid_edges[:]
                            new_dest_is_valid = False
                            new_rand_route = None
                            new_dest_edge = random.choice(valid_edges_copy)
                            while not new_dest_is_valid:
                                new_dest_edge = random.choice(valid_edges_copy)
                                new_rand_route = traci.simulation.findRoute(self.empty_taxis[taxi_id], new_dest_edge, vType="car")
                                if new_rand_route and new_rand_route.edges and new_dest_edge != self.empty_taxis[taxi_id]:
                                    new_dest_is_valid = True
                                else:
                                    valid_edges_copy.remove(new_dest_edge)
                                    #print(f"Num edges: {len(valid_edges)}")
                                    if len(valid_edges_copy)==0:
                                        # print(f"{taxi_id} wound up on an unreachable edge")
                                        break
                            if new_dest_is_valid:
                                traci.vehicle.setRoute(taxi_id, new_rand_route.edges)
                                self.empty_taxis[taxi_id] = new_dest_edge
                                # print(f"\t{taxi_id} has a new route {new_rand_route.edges}")
                            else:
                                # print(f"\tBecause {taxi_id} wound up on an unreachable edge, putting it out of commission for half the time of an out-of-battery tow")
                                self.out_of_commission[taxi_id] = [simulation_time + 150, float(traci.vehicle.getParameter(taxi_id, 'device.battery.actualBatteryCapacity'))]
                                traci.vehicle.remove(taxi_id)
                                delete_from_empty_taxis.append(taxi_id)
                for taxi_id in delete_from_empty_taxis:
                    del self.empty_taxis[taxi_id]

                # This code block periodically outputs significant data, such as profits and electricity consumption
                if simulation_time >= self.all_significant_data_update_time or simulation_time + self.step_length == self.sim_end_time: # update this line to have these important statistics print more frequently
                    if self.optimized:
                        print("Optimized Version:")
                    else:
                        print("Control Version:")
                    total_satisfied_reservations = 0
                    total_earnings = 0
                    print(f"The base price is {self.taxi_ride_base_price} and the distance rate is {self.taxi_ride_distance_rate}")
                    print(f"Demand Multipliers: {self.demand_multipliers}")
                    print(f"TOD Rates: {self.tod_rate}")
                    for taxi_id in self.taxi_ids:
                        taxi_earnings = 0
                        if taxi_id in self.completed_reservations_by_taxi.keys():
                            total_satisfied_reservations += len(self.completed_reservations_by_taxi[taxi_id])
                            for completed_trip in self.completed_reservations_by_taxi[taxi_id]:
                                trip_length = completed_trip[0] / 1000  # in km
                                trip_demand_multiplier = completed_trip[1]
                                trip_tod_rate = completed_trip[2]
                                # print(f"\t\tTaxi {taxi_id} completed a reservation of length {trip_length} km when the demand mult was {trip_demand_multiplier} and the tod rate was {trip_tod_rate}")
                                trip_earnings = self.taxi_ride_base_price + (
                                            (trip_length * self.taxi_ride_distance_rate) * trip_demand_multiplier * trip_tod_rate)
                                # print(f"\t\t\tThis trip earned ${trip_earnings}")
                                taxi_earnings += trip_earnings
                        # print(f"\tTaxi Earnings {taxi_id}: ${taxi_earnings}")
                        total_earnings += taxi_earnings
                    print(f"Electricity Costs: {self.electricity_costs}")
                    total_num_charging_trips = 0
                    total_num_tows = 0
                    total_cost = 0
                    print(f"Charge Base Price: {self.charge_base_price}")
                    for taxi_id in self.taxi_ids:
                        taxi_cost = 0
                        if taxi_id in self.cost_per_charging_trip.keys():
                            total_num_charging_trips += len(self.cost_per_charging_trip[taxi_id])
                            taxi_cost += (len(self.cost_per_charging_trip[taxi_id])*self.charge_base_price) + sum(self.cost_per_charging_trip[taxi_id])
                        if taxi_id in self.cost_per_tow.keys():
                            total_num_tows += len(self.cost_per_tow[taxi_id])
                            taxi_cost += (len(self.cost_per_tow[taxi_id]) * self.tow_base_price) + sum(self.cost_per_tow[taxi_id])
                        # print(f"\tTaxi Cost {taxi_id}: ${taxi_cost}")
                        total_cost += taxi_cost
                    
                    print(f"Total completed reservations: {total_satisfied_reservations} reservations")
                    print(f"Total Earnings: ${total_earnings}")
                    if total_satisfied_reservations > 0:
                        print(f"Average Earnings from One Taxi Ride: ${total_earnings / total_satisfied_reservations}")
                    else:
                        print("Average Earnings from One Taxi Ride: $0")
                    print(f"Average Earnings of One Taxi: ${total_earnings / len(self.taxi_ids)}")
                    print(f"Total charging trips: {total_num_charging_trips} charging trips")
                    print(f"Total Cost: ${total_cost}")
                    print(f"Average Cost of One Taxi: {total_cost / len(self.taxi_ids)}")
                    print(f"Total Profits: ${total_earnings - total_cost}")
                    print(f"Average Profits of One Taxi: ${(total_earnings - total_cost) / len(self.taxi_ids)}")
                    print(f"Number of times a taxi ran out of battery: {total_num_tows}")
                    total_distance_driven = sum(self.total_distance_driven_per_taxi.values())
                    print(f"Total Distance Driven: {total_distance_driven} km")
                    print(f"Average Distance Driven per Taxi: {total_distance_driven / len(self.taxi_ids)} km")
                    if len(self.reservation_wait_times.values()) > 0:
                        print(f"Average Wait Time per Reservation: {sum(self.reservation_wait_times.values())/len(self.reservation_wait_times.values())}")
                        print(f"Minimum Wait Time: {min(self.reservation_wait_times.values())}")
                        print(f"Maximum Wait Time: {max(self.reservation_wait_times.values())}")
                    else:
                        print("Average Wait Time per Reservation: N/A, no reservations picked up")
                    print(f"Total electricity consumption: {sum(self.electricity_consumption_per_taxi.values())/1000} kWh")
                    print(f"Average Electricity consumption by taxi: {(sum(self.electricity_consumption_per_taxi.values())/1000) / len(self.taxi_ids)} kWh")
                    self.all_significant_data_update_time += self.output_freq

                # Increment the timestep
                simulation_time += self.step_length
                
                # Optional: Add a short delay to prevent overloading
                time.sleep(0.01)

            except traci.exceptions.TraCIException as e:
                print(f"TraCI error during simulation loop at timestep {simulation_time}: {e}")
                break
            except Exception as e:
                print(f"Unexpected error during simulation loop at timestep {simulation_time}: {e}")
                break

        print("Exiting simulation loop.")
    
    def load_historical_data(self, file_path):
        """
        Load the provided historical electricity cost data

        Args:
        - file_path: Path to the data file

        Returns:
        - Pandas DataFrame with historical data
        """
        try:
            db = pd.read_excel(file_path)
            return db
        except Exception as e:
            print(f"Error reading the file: {e}")
            return None

    def get_hist_data(self, database):
        """
        Process the DataFrame for use by predictive models

        Args:
        - database: Pandas DataFrame with historical data

        Returns:
        - The data separated into x-values (day, time of day) and y-values (price of charging vehicles in $/kWh)
        """
        features = [database.columns[0], database.columns[1]]
        elec_price = database.columns[2] # prices given in $/kWh
        x_vals = database[features]
        y_vals = database[elec_price]
        return x_vals, y_vals

    def train_prediction_models(self, x_vals, y_vals):
        """
        Train predictive models using historical data

        Args:
        - x_vals: Matrix that stores the day and time of day
        - y_vals: corresponding price of charging vehicles in $/kWh

        Returns:
        - Trained models and scalers
        """
        scaler_obj = StandardScaler()
        x_scaled = scaler_obj.fit_transform(x_vals)
        price_model = RandomForestRegressor(n_estimators=300, max_depth=10, min_samples_split=10, min_samples_leaf=1)
        price_model.fit(x_scaled, y_vals)
        return [price_model, scaler_obj]

    
    def clear_residual_variables(self):
        """
        Sometimes from one run to another, TraCI doesn't immediately update the number of people and chargers. This function forces a clear slate
        I tried alternatively deleting all the people at the end of each simulation but that did not work. This current solution, although
        a bit redundant and time expensive, seems to be the only option
        """
        for person_id in self.person_ids:
            with suppress(Exception):
                traci.person.remove(person_id)
        for person_id in traci.person.getIDList():
            traci.person.remove(person_id)

    def set_time_dependent_price_variables(self, simulation_time):
        """
        Determines the demand multipliers and time of day rates used to calculate taxi earnings, and the electricity prices used to calculate taxi costs, based on the current time of day
        The demand multiplier is based both on real-world demand trends as well as live updates within the simulation (which allows it to account for sudden surges dynamically caused by the user)
        The time of day rates and the price of charging vehicles are also based on real-world values

        Args:
        - simulation_time: The current time in seconds within the simulation
        """
        if simulation_time == 0 or (simulation_time < 1200 and len(self.demand_multipliers)==0): # represents 12am-4am
            self.recent_reservations.append(self.new_res_counter/2.0)
            self.recent_reservations.append(self.recent_reservations[-1])
            base_demand_mult = random.randint(50,70)/100
            self.demand_multipliers.append(self.calculate_demand_multiplier(base_demand_mult))

            self.tod_rate.append(self.tod_rate_normal)
            self.electricity_costs.append(random.randint(25,28)/100)
        elif simulation_time == 1200 or (simulation_time < 1800 and len(self.demand_multipliers)==0): # represents 4am-6am
            self.recent_reservations.append(self.new_res_counter)
            base_demand_mult = random.randint(70,100)/100
            self.demand_multipliers.append(self.calculate_demand_multiplier(base_demand_mult))

            self.tod_rate.append(self.tod_rate_normal)
            self.electricity_costs.append(random.randint(28, 32) / 100)
        elif simulation_time == 1800 or (simulation_time < 2400 and len(self.demand_multipliers)==0): # represents 6am-8am, morning rush hour
            self.recent_reservations.append(self.new_res_counter)
            base_demand_mult = random.randint(150,200)/100
            self.demand_multipliers.append(self.calculate_demand_multiplier(base_demand_mult))

            self.tod_rate.append(self.tod_rate_morning_rush)
            self.electricity_costs.append(random.randint(35, 45) / 100)
        elif simulation_time == 2400 or (simulation_time < 3000 and len(self.demand_multipliers)==0): # represents 8am-10am
            self.recent_reservations.append(self.new_res_counter)
            base_demand_mult = random.randint(100,130)/100
            self.demand_multipliers.append(self.calculate_demand_multiplier(base_demand_mult))

            self.tod_rate.append(self.tod_rate_normal)
            self.electricity_costs.append(random.randint(35, 45) / 100)
        elif simulation_time == 3000 or (simulation_time < 4200 and len(self.demand_multipliers)==0): # represents 10am-2pm, includes lunch transportation
            self.recent_reservations.append(self.new_res_counter/2.0)
            self.recent_reservations.append(self.recent_reservations[-1])
            base_demand_mult = random.randint(110,150)/100
            self.demand_multipliers.append(self.calculate_demand_multiplier(base_demand_mult))

            self.tod_rate.append(self.tod_rate_normal)
            self.electricity_costs.append(random.randint(32, 38) / 100)
        elif simulation_time == 4200 or (simulation_time < 4800 and len(self.demand_multipliers)==0): # represents 2pm-4pm
            self.recent_reservations.append(self.new_res_counter)
            base_demand_mult = random.randint(80,110)/100
            self.demand_multipliers.append(self.calculate_demand_multiplier(base_demand_mult))

            self.tod_rate.append(self.tod_rate_normal)
            self.electricity_costs.append(random.randint(32, 38) / 100)
        elif simulation_time == 4800 or (simulation_time < 5400 and len(self.demand_multipliers)==0): # represents 4pm-6pm, evening rush hour
            self.recent_reservations.append(self.new_res_counter)
            base_demand_mult = random.randint(150,220)/100
            self.demand_multipliers.append(self.calculate_demand_multiplier(base_demand_mult))

            self.tod_rate.append(self.tod_rate_evening_rush)
            self.electricity_costs.append(random.randint(45, 60) / 100)
        elif simulation_time == 5400 or (simulation_time < 6000 and len(self.demand_multipliers)==0): # represents 6pm-8pm
            self.recent_reservations.append(self.new_res_counter)
            base_demand_mult = random.randint(120,150)/100
            self.demand_multipliers.append(self.calculate_demand_multiplier(base_demand_mult))

            self.tod_rate.append(self.tod_rate_normal)
            self.electricity_costs.append(random.randint(45, 60) / 100)
        elif simulation_time == 6000 or (simulation_time < 6600 and len(self.demand_multipliers)==0): # represents 8pm-10pm
            self.recent_reservations.append(self.new_res_counter)
            base_demand_mult = random.randint(100,130)/100
            self.demand_multipliers.append(self.calculate_demand_multiplier(base_demand_mult))

            self.tod_rate.append(self.tod_rate_normal)
            self.electricity_costs.append(random.randint(45, 60) / 100)
        elif simulation_time == 6600 or (simulation_time < 7200 and len(self.demand_multipliers)==0): # represents 10pm-11:59pm
            self.recent_reservations.append(self.new_res_counter)
            base_demand_mult = random.randint(70,100)/100
            self.demand_multipliers.append(self.calculate_demand_multiplier(base_demand_mult))

            self.tod_rate.append(self.tod_rate_normal)
            self.electricity_costs.append(random.randint(30, 35) / 100)
        
    def calculate_demand_multiplier(self, base_demand_mult):
        """
        Calculates the demand multiplier based on real-world demand trends as well as live updates within the simulation of the number of reservations added
        within the past six hours

        Args:
        - base_demand_mult: From real-world trends, represents what the demand multiplier usually is at the current time of day

        Returns:
        - a demand multiplier value that is scaled to account for live updates within the simulation and bounded within 0.5 and 2.5
        """
        while len(self.recent_reservations) > 3: # 3 data points represents six hours
            self.recent_reservations.pop(0)
        avg_new_res = sum(self.recent_reservations) / len(self.recent_reservations)
        if avg_new_res > 0:
            demand_multiplier = base_demand_mult * (self.new_res_counter / avg_new_res)
        else:
            demand_multiplier = 0.5
        self.new_res_counter = 0
        return min(max(demand_multiplier, 0.5), 2.5)
    
    def reset_taxi_loc(self, taxi_id, battery_level):
        """
        Adds a taxi that was out of commission back into the simulation. Initializes it with a random route

        Args:
        - taxi_id: The taxi to add back into the simulation
        - battery_level: The amount of charge the taxi should have when it is reinitialized
        """
        edges_are_valid = False
        rand_route = None
        start_edge_id = random.choice(self.valid_edges)
        dest_edge_id = random.choice(self.valid_edges)
        while not edges_are_valid:
            rand_route = traci.simulation.findRoute(start_edge_id, dest_edge_id, vType="car")
            if rand_route and rand_route.edges and dest_edge_id != start_edge_id:
                edges_are_valid = True
            else:
                start_edge_id = random.choice(self.valid_edges)
                dest_edge_id = random.choice(self.valid_edges)
        route_id = f"route_{self.extra_route_counter}"
        self.extra_route_counter += 1
        traci.route.add(route_id, rand_route.edges)
        traci.vehicle.add(taxi_id, routeID=route_id, typeID="car", departPos="random", departLane="best", departSpeed="max")
        self.empty_taxis[taxi_id] = dest_edge_id
        traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", battery_level)  # Wh
        traci.vehicle.setParameter(taxi_id, "device.battery.maximumBatteryCapacity", 8000)  # Wh
        traci.vehicle.setColor(taxi_id, (0,255,0))
        print(f"Reset taxi: Spawned taxi {taxi_id} with {battery_level} Wh charge")

    def reset_res(self, res_id, curr_edge, curr_pos, simulation_time):
        """
        If a taxi dies while it is carrying a passenger, this function resets the passenger's reservation at the location where the taxi died

        Args:
        - res_id: The reservation ID of the passenger that needs to be reset
        - curr_edge: The edge of the map along which the taxi died
        - curr_pos: The position along curr_edge at which the taxi died
        - simulation_time: The time at which the taxi died and the passenger needs to be reset
        """
        person_id = self.all_valid_res[res_id][0]
        new_route = traci.simulation.findRoute(curr_edge, self.all_valid_res[res_id][2], vType="car")
        new_assignment = [person_id, curr_edge, self.all_valid_res[res_id][2], curr_pos, self.all_valid_res[res_id][4], simulation_time, new_route.edges, new_route.length]
        self.all_valid_res[res_id] = new_assignment
        self.new_res_counter -= 1 # without this line, the reset reservation would be counted twice
        print(f"Reset reservation: Person {person_id} re-added with ride from {curr_edge} to {self.all_valid_res[res_id][2]}")
        
    def reinit_res(self, res_id, person_id, depart_time):
        """
        If a passenger was initialized on a section of the map where no taxis can reach it, it is reinitialized with the goal of setting it on a reachable edge

        Args:
        - res_id: The reservation ID of the passenger that needs to be reinitialized
        - person_id: The ID of the passenger that needs to be reinitialized
        - depart_time: The current simulation time, the time at which the passenger should be reinitialized
        """
        edges_are_valid = False
        pickup_edge_id = random.choice(self.valid_edges)
        dropoff_edge_id = random.choice(self.valid_edges)
        curr_route = None
        while not edges_are_valid:
            curr_route = traci.simulation.findRoute(pickup_edge_id, dropoff_edge_id, vType="car")
            if curr_route and curr_route.edges and dropoff_edge_id != pickup_edge_id:
                edges_are_valid = True
            else:
                pickup_edge_id = random.choice(self.valid_edges)
                dropoff_edge_id = random.choice(self.valid_edges)
        pickup_lane = self.net.getEdge(pickup_edge_id).getLanes()[0]
        dropoff_lane = self.net.getEdge(dropoff_edge_id).getLanes()[0]
        pickup_pos = random.uniform(max(pickup_lane.getLength()*(1/4), 13), min(pickup_lane.getLength()*(3/4), pickup_lane.getLength()-13))
        dropoff_pos = random.uniform(max(dropoff_lane.getLength()*(1/4), 13), min(dropoff_lane.getLength()*(3/4), dropoff_lane.getLength()-13))
        self.all_valid_res[res_id] = [person_id, pickup_edge_id, dropoff_edge_id, pickup_pos, dropoff_pos, depart_time, curr_route.edges, curr_route.length]
        self.new_res_counter -= 1 # without this line, the reset reservation would be counted twice


    def optimized_charging(self, taxi_id, models, sim_time, curr_bat):
        """
        Predicts future electricity prices for the next six hours to determine if a taxi that is starting to run low on battery should charge now

        Args:
        - taxi_id: The ID of the taxi that is considering charging
        - models: The predictive models trained from provided historical data
        - sim_time: The current simulation time
        - curr_bat: The taxi's current battery level

        Returns:
        - a boolean value representing the charging decision (True if the taxi should charge now, False if it should not)
        """
        future_prices = self.predict_future_prices(models, sim_time)
        curr_price = self.electricity_costs[-1]
        # print(f"Current electricity price: {curr_price}")
        min_battery_threshold = 550 # Wh - if battery is below this amount, must charge
        if curr_bat < min_battery_threshold:
            traci.vehicle.setColor(taxi_id, (255, 165, 0))  # taxis turn orange when they reach low charge
            # print(f"{taxi_id} urgently needs charge")
            return True
        if future_prices:
            higher_price_count = sum(1 for price in future_prices.values() if price > curr_price)
            num_future_prices = len(future_prices)
            if higher_price_count / num_future_prices > 0.6:
                # print("most future electricity prices are higher, charging now")
                return True
        return False


    def predict_future_prices(self, models, sim_time, forecast=6):
        """
        Uses predictive models to guess what future electricity prices will be for charging vehicles. By default, makes predictions six hours into the future

        Args:
        - models: The predictive models trained from provided historical data
        - sim_time: The current simulation time
        - forecast: Number of hours into the future for which predictions should be made

        Returns:
        - predicted electricity prices
        """
        predictions = {}
        # print("\tPredicting future prices:")
        for increase_val in range(forecast):
            future_time = sim_time + (increase_val+1)*300 # because the sim is scaled so that 300 seconds represents one hour
            if future_time >= 7200:
                future_time = future_time - 7200
            features = pd.DataFrame({'Day Number': [2501], 'Simulation Time': [future_time]}) # historical data goes up to day 2500, so current day is set to 2501
            elec_price_features = models[1].transform(features)
            predicted_price = models[0].predict(elec_price_features)[0]
            # print(f"\t\tfuture time: {future_time}, predicted price: {predicted_price}")
            predictions[increase_val+1] = float(predicted_price)
        # print(f"\tPredictions: {predictions}")
        return predictions
    
    def find_nearest_charger(self, chargers_to_use, taxis_to_charge):
        """
        Assigns taxis that need to charge to their nearest active chargers

        Args:
        - chargers_to_use: A list of the chargers that are currently active
        - taxis_to_charge: The list of taxis that need to charge

        Returns:
        - Assignments mapping each taxi to the charger it should use
        """
        assignments = {}
        for taxi_id in taxis_to_charge:
            curr_edge = traci.vehicle.getRoadID(taxi_id)
            nearest_charger_id = ""
            shortest_length = float('inf')
            shortest_route = None
            for charger_info in chargers_to_use:
                charger_id = charger_info[0]
                charger_lane = charger_info[1]
                charger_edge = traci.lane.getEdgeID(charger_lane)
                route_to_charger = traci.simulation.findRoute(curr_edge, charger_edge, vType="car")
                if route_to_charger is not None and len(route_to_charger.edges) != 0:
                    if route_to_charger.length < shortest_length:
                        shortest_length = route_to_charger.length
                        nearest_charger_id = charger_id
                        shortest_route = route_to_charger
            if shortest_route is not None:
                assignment = [nearest_charger_id, shortest_length, shortest_route]
                assignments[taxi_id] = assignment
        # if len(assignments) != 0:
        #     print(f"{len(assignments)} taxis successfully assigned to chargers")
        return assignments
    
    def efficient_taxi_assignment(self, pending_reservations, available_taxis, sim_time):
        """
        Assigns taxis that can pick up reservations to their closest unassigned pending reservation
        Non-optimized version: Each taxi is assigned to its closest unassigned reservation, but the order in which taxis are assigned is random
        Assignment works in two steps:
        - check if any reservations are unreachable or any taxis are stuck on inaccessible sections of the map
        - assign the available reservations and taxis based on route distance

        Args:
        - pending_reservations: A list of reservation IDs for the pending reservations
        - available_taxis: The list of taxis that can be assigned to reservations
        - sim_time: The current simulation time

        Returns:
        - Assignments mapping each taxi to the reservation it should pick up
        """
        random.shuffle(available_taxis)
        assignments = {}
        assigned_res = set()
        unreached_this_step = []
        route_cache = {}
        put_out_of_commission = []
        count_taxi_reachability = {}
        # print("Checking Reachability:")
        for res_id in pending_reservations:
            is_reachable = False
            for taxi_id in available_taxis:
                if taxi_id not in count_taxi_reachability.keys():
                    count_taxi_reachability[taxi_id] = 0
                cache_key = (taxi_id, res_id)
                if cache_key not in route_cache.keys():
                    route_cache[cache_key] = traci.simulation.findRoute(traci.vehicle.getRoadID(taxi_id), self.all_valid_res[res_id][1], vType="car")
                if route_cache[cache_key].edges:
                    is_reachable = True
                    count_taxi_reachability[taxi_id] += 1
            if not is_reachable:
                self.unreached_reservations.append(res_id)
                unreached_this_step.append(res_id)
        for taxi_id in available_taxis:
            if count_taxi_reachability[taxi_id] == 0 and count_taxi_reachability[taxi_id] != len(pending_reservations)-len(unreached_this_step):
                print(f"\t{taxi_id} CANNOT REACH ANY PASSENGERS AND IS BEING TAKEN OUT OF COMMISSION")
                put_out_of_commission.append(taxi_id)
                curr_bat = float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity"))
                self.out_of_commission[taxi_id] = [sim_time+150, curr_bat]
                traci.vehicle.remove(taxi_id)
        # print("Assignments:")
        for taxi_id in available_taxis:
            if taxi_id not in put_out_of_commission:
                nearest_res_id = -1
                shortest_length = float('inf')
                shortest_route = None
                for res_id in pending_reservations:
                    if res_id not in assigned_res and res_id not in unreached_this_step:
                        cache_key = (taxi_id, res_id)
                        if cache_key in route_cache.keys():
                            route_to_pickup = route_cache.get(cache_key)
                        else:
                            route_to_pickup = traci.simulation.findRoute(traci.vehicle.getRoadID(taxi_id), self.all_valid_res[res_id][1], vType="car")
                        if route_to_pickup and route_to_pickup.edges:
                            if route_to_pickup.length < shortest_length:
                                shortest_length = route_to_pickup.length
                                nearest_res_id = res_id
                                shortest_route = route_to_pickup
                if nearest_res_id >= 0:
                    assignment = [nearest_res_id, shortest_length, shortest_route]
                    assignments[taxi_id] = assignment
                    assigned_res.add(nearest_res_id)
        for res_id in unreached_this_step:
            self.waiting_reservations.remove(res_id)
        for taxi_id in put_out_of_commission:
            del self.empty_taxis[taxi_id]
        # if len(assignments) != 0:
        #     print(f"{len(assignments)} taxis successfully assigned to reservations")
        return assignments

    def optimized_taxi_assignment(self, pending_reservations, available_taxis, sim_time):
        """
        Assigns taxis that can pick up reservations to their closest unassigned pending reservation
        Optimized version: Taxi assignment minimizes driving distance
        Assignment works in two steps:
        - check if any reservations are unreachable or any taxis are stuck on inaccessible sections of the map
        - assign the available reservations and taxis based on route distance

        Args:
        - pending_reservations: A list of reservation IDs for the pending reservations
        - available_taxis: The list of taxis that can be assigned to reservations
        - sim_time: The current simulation time

        Returns:
        - Assignments mapping each taxi to the reservation it should pick up
        """
        assignments = {}
        available_res = {}
        unassigned_taxis = []
        unassigned_res = []
        unreached_this_step = []
        route_cache = {}
        put_out_of_commission = []
        count_taxi_reachability = {}
        # print("Checking Reachability:")
        for res_id in pending_reservations:
            is_reachable = False
            for taxi_id in available_taxis:
                if taxi_id not in count_taxi_reachability.keys():
                    count_taxi_reachability[taxi_id] = 0
                cache_key = (taxi_id, res_id)
                if cache_key not in route_cache.keys():
                    route_cache[cache_key] = traci.simulation.findRoute(traci.vehicle.getRoadID(taxi_id), self.all_valid_res[res_id][1], vType="car")
                if route_cache[cache_key].edges:
                    is_reachable = True
                    count_taxi_reachability[taxi_id] += 1
            if not is_reachable:
                self.unreached_reservations.append(res_id)
                unreached_this_step.append(res_id)
            else:
                unassigned_res.append(res_id)
        for taxi_id in available_taxis:
            if count_taxi_reachability[taxi_id] == 0 and count_taxi_reachability[taxi_id] != len(pending_reservations) - len(unreached_this_step):
                print(f"\t{taxi_id} CANNOT REACH ANY PASSENGERS AND IS BEING TAKEN OUT OF COMMISSION")
                put_out_of_commission.append(taxi_id)
                curr_bat = float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity"))
                self.out_of_commission[taxi_id] = [sim_time + 150, curr_bat]
                traci.vehicle.remove(taxi_id)
            else:
                unassigned_taxis.append(taxi_id)
                available_res[taxi_id] = [res_id for res_id in pending_reservations if res_id not in unreached_this_step]
        # print("Assignments:")
        if len(available_taxis)-len(put_out_of_commission) <= len(pending_reservations)-len(unreached_this_step):
            while len(assignments.keys()) < len(available_taxis)-len(put_out_of_commission):
                taxi_id = random.choice(unassigned_taxis)
                nearest_res_id = -1
                shortest_length = float('inf')
                shortest_route = None
                for res_id in available_res[taxi_id]:
                    cache_key = (taxi_id, res_id)
                    if cache_key not in route_cache.keys():
                        route_cache[cache_key] = traci.simulation.findRoute(traci.vehicle.getRoadID(taxi_id),self.all_valid_res[res_id][1], vType="car")
                    route_to_pickup = route_cache[cache_key]
                    if route_to_pickup and route_to_pickup.edges:
                        if route_to_pickup.length < shortest_length:
                            shortest_length = route_to_pickup.length
                            nearest_res_id = res_id
                            shortest_route = route_to_pickup
                if nearest_res_id >= 0:
                    already_assigned = False
                    for dict_key in assignments.keys():
                        dict_val = assignments[dict_key]
                        if nearest_res_id == dict_val[0]:
                            if shortest_length<dict_val[1]:
                                del assignments[dict_key]
                                unassigned_taxis.append(dict_key)
                                available_res[dict_key].remove(nearest_res_id)
                                assignment = [nearest_res_id, shortest_length, shortest_route]
                                assignments[taxi_id] = assignment
                                unassigned_taxis.remove(taxi_id)
                            else:
                                available_res[taxi_id].remove(nearest_res_id)
                            already_assigned = True
                            break
                    if not already_assigned:
                        assignment = [nearest_res_id, shortest_length, shortest_route]
                        assignments[taxi_id] = assignment
                        unassigned_taxis.remove(taxi_id)
        else:
            while len(assignments.keys()) < len(pending_reservations)-len(unreached_this_step):
                res_id = random.choice(unassigned_res)
                nearest_taxi_id = ""
                shortest_length = float('inf')
                shortest_route = None
                for taxi_id in available_taxis:
                    if taxi_id in available_res.keys() and res_id in available_res[taxi_id]:
                        cache_key = (taxi_id, res_id)
                        if cache_key not in route_cache.keys():
                            route_cache[cache_key] = traci.simulation.findRoute(traci.vehicle.getRoadID(taxi_id),self.all_valid_res[res_id][1], vType="car")
                        route_to_pickup = route_cache[cache_key]
                        if route_to_pickup and route_to_pickup.edges:
                            if route_to_pickup.length < shortest_length:
                                shortest_length = route_to_pickup.length
                                nearest_taxi_id = taxi_id
                                shortest_route = route_to_pickup
                if len(nearest_taxi_id) != 0:
                    if nearest_taxi_id in assignments.keys():
                        dict_val = assignments[nearest_taxi_id]
                        if shortest_length < dict_val[1]:
                            del assignments[nearest_taxi_id]
                            unassigned_res.append(dict_val[0])
                            available_res[nearest_taxi_id].remove(dict_val[0])
                            assignment = [res_id, shortest_length, shortest_route]
                            assignments[nearest_taxi_id] = assignment
                            unassigned_res.remove(res_id)
                        else:
                            available_res[nearest_taxi_id].remove(res_id)
                    else:
                        assignment = [res_id, shortest_length, shortest_route]
                        assignments[nearest_taxi_id] = assignment
                        unassigned_res.remove(res_id)
        for res_id in unreached_this_step:
            self.waiting_reservations.remove(res_id)
        for taxi_id in put_out_of_commission:
            del self.empty_taxis[taxi_id]
        # if len(assignments) != 0:
        #     print(f"{len(assignments)} taxis successfully assigned to reservations")
        return assignments

    def cleanup(self):
        """Safely cleans up the simulation environment."""
        print("Cleaning up simulation...")
        try:
            if traci.isLoaded():
                traci.close()
                print("Closed SUMO connection.")
        except traci.exceptions.TraCIException as e:
            print(f"Error closing SUMO connection: {e}")
        except Exception as e:
            print(f"Unexpected error during cleanup: {e}")
        finally:
            self.is_running = False
            print("Simulation cleanup complete.\n\n\n")


    def _add_people(self, num_people):
        """
        Dynamically adds reservations, the corresponding people will be added to the simulation in the next time step

        Args:
        - num_people: The number of people to add
        """
        print(f"Adding {num_people} people dynamically...")
        # for _ in range(num_people):
        #     start_edge = random.choice(self.valid_edges)
        #     end_edge = random.choice(self.valid_edges)
        #     if start_edge != end_edge:
        #         person_id = f"person_dyn_{self.person_counter}"
        #         self.person_counter += 1
        #         self.person_ids.append(person_id)
        #         traci.person.add(person_id, start_edge, pos=0, depart=traci.simulation.getTime() + self.step_length)
        #         traci.person.appendDrivingStage(person_id, toEdge=end_edge, lines="taxi")
        #         print(f"Dynamically added person {person_id} from {start_edge} to {end_edge}")
        
        for _ in range(num_people): # with this version, it's not necessary to call traci.person.add here, because the simulation loop will take care of that
            edges_are_valid = False
            pickup_edge_id = random.choice(self.valid_edges)
            dropoff_edge_id = random.choice(self.valid_edges)
            curr_route = None
            while not edges_are_valid:
                curr_route = traci.simulation.findRoute(pickup_edge_id, dropoff_edge_id, vType="car")
                if curr_route and curr_route.edges and dropoff_edge_id != pickup_edge_id:
                    edges_are_valid = True
                else:
                    pickup_edge_id = random.choice(self.valid_edges)
                    dropoff_edge_id = random.choice(self.valid_edges)
            person_id = f"person_{self.person_counter}"
            self.person_counter += 1
            self.person_ids.append(person_id)
            depart_time = traci.simulation.getTime()-self.traci_start_time
            pickup_lane = self.net.getEdge(pickup_edge_id).getLanes()[0]
            dropoff_lane = self.net.getEdge(dropoff_edge_id).getLanes()[0]
            pickup_pos = random.uniform(max(pickup_lane.getLength()*(1/4), 13), min(pickup_lane.getLength()*(3/4), pickup_lane.getLength()-13))
            dropoff_pos = random.uniform(max(dropoff_lane.getLength()*(1/4), 13), min(dropoff_lane.getLength()*(3/4), dropoff_lane.getLength()-13))
            res_id = self.person_counter-1
            self.all_valid_res[res_id] = [person_id, pickup_edge_id, dropoff_edge_id, pickup_pos, dropoff_pos, depart_time, curr_route.edges, curr_route.length]

    def _add_chargers_at_runtime(self, num_chargers):
        """
        Dynamically adds chargers. Induction loops (detectors) cannot be added dynamically through TraCI, so new chargers will not show up in the simulation

        Args:
        - num_chargers: The number of chargers to add
        """
        print(f"Adding {num_chargers} chargers dynamically...")
        # for _ in range(num_chargers):
        #     edge_id = random.choice(self.valid_edges)
        #     lane = random.choice(self.net.getEdge(edge_id).getLanes())
        #     position = random.uniform(0, lane.getLength())
        #     charger_id = f"charger_dyn_{self.charger_counter}"
        #     self.charger_counter += 1
        #     self.active_chargers.append((charger_id, lane.getID(), position))
        #     print(f"Dynamically added charger {charger_id} on lane {lane.getID()} at position {position}")
        
        for _ in range(num_chargers):
            edge_id = random.choice(self.valid_edges)
            lane = self.net.getEdge(edge_id).getLanes()[0]
            lane_length = lane.getLength()
            lane_pos = random.uniform(max(lane_length*(1/4), 13), min(lane_length*(3/4), lane_length-13))
            charger_id = f"charger_{self.charger_counter}"
            self.charger_counter += 1
            self.active_chargers.append((charger_id, lane.getID(), lane_pos))

    def _spawn_taxis_at_runtime(self, num_taxis):
        """
        Dynamically adds taxis to the simulation and initializes them with random routes

        Args:
        - num_taxis: The number of people to add
        """
        print(f"Adding {num_taxis} taxis dynamically...")
        # for _ in range(num_taxis):
        #     start_edge = random.choice(self.valid_edges)
        #     try:
        #         taxi_id = f"taxi_dyn_{self.taxi_counter}"
        #         self.taxi_counter += 1
        #         self.taxi_ids.append(taxi_id)
        #         traci.route.add(f"route_{taxi_id}", [start_edge])
        #         traci.vehicle.add(
        #             taxi_id,
        #             routeID=f"route_{taxi_id}",
        #             typeID="car",
        #             departPos="random",
        #             departLane="best",
        #             departSpeed="max",
        #         )
        #         print(f"Dynamically added taxi {taxi_id}")
        #     except traci.exceptions.TraCIException as e:
        #         print(f"Error adding taxi dynamically: {e}")

        for _ in range(num_taxis):
            edges_are_valid = False
            rand_route = None
            start_edge_id = random.choice(self.valid_edges)
            dest_edge_id = random.choice(self.valid_edges)
            while not edges_are_valid:
                rand_route = traci.simulation.findRoute(start_edge_id, dest_edge_id, vType="car")
                if rand_route and rand_route.edges and dest_edge_id != start_edge_id:
                    edges_are_valid = True
                else:
                    start_edge_id = random.choice(self.valid_edges)
                    dest_edge_id = random.choice(self.valid_edges)
            taxi_id = f"taxi_{self.taxi_counter}"
            self.taxi_counter += 1
            route_id = f"route_{taxi_id}"
            traci.route.add(route_id, rand_route.edges)
            traci.vehicle.add(taxi_id, routeID=route_id, typeID="car", departPos="random", departLane="best", departSpeed="max")
            self.empty_taxis[taxi_id] = dest_edge_id
            charge_amount = random.randint(7,60)
            charge_amount = charge_amount*100
            traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", charge_amount) #Wh
            traci.vehicle.setParameter(taxi_id, "device.battery.maximumBatteryCapacity", 8000)  # Wh
            self.taxi_ids.append(taxi_id)

    def _remove_people(self, num_people):
        """
        Dynamically removes people from the simulation. Will only remove people who are already in the simulation (depart time is in the past)
        but have not yet been assigned to taxis

        Args:
        - num_people: The maximum number of people to try to remove from the simulation
        """
        print(f"Attempting to remove {num_people} people dynamically...")
        # for _ in range(num_people):
        #     try:
        #         # Fetch active and valid person IDs
        #         active_person_ids = [pid for pid in traci.person.getIDList() if pid in self.person_ids]

        #         # Validate removable people: not in a taxi or reserved
        #         removable_person_ids = [
        #             pid for pid in active_person_ids
        #             if not traci.person.getTaxiReservations(pid) and not traci.person.getVehicle(pid)
        #         ]

        #         if removable_person_ids:
        #             person_id = removable_person_ids.pop(0)
        #             try:
        #                 traci.person.remove(person_id)  # Remove from SUMO
        #                 self.person_ids.remove(person_id)  # Remove from local tracking
        #                 print(f"Successfully removed person {person_id} from the simulation.")
        #             except traci.exceptions.TraCIException as e:
        #                 print(f"TraCI error while removing person {person_id}: {e}")
        #             except Exception as e:
        #                 print(f"Unexpected error while removing person {person_id}: {e}")
        #         else:
        #             print("No removable people found (all reserved or in taxis).")
        #             break
        #     except Exception as e:
        #         print(f"Unexpected error during person removal: {e}")

        # print(f"Finished attempting to remove {num_people} people.")
        
        count_removed_people = 0
        try:
            # Dict of removable people: anyone who is already in the simulation but who has not yet been assigned
            removable_person_ids = {}
            for res_id in self.waiting_reservations:
                removable_person_ids[self.all_valid_res[res_id][0]] = res_id
            for _ in range(num_people):
                if removable_person_ids:
                    person_id = random.choice(list(removable_person_ids.keys()))
                    try:
                        traci.person.remove(person_id)  # Remove from SUMO
                        self.person_ids.remove(person_id)  # Remove from local tracking
                        del self.all_valid_res[removable_person_ids[person_id]]
                        self.waiting_reservations.remove(removable_person_ids[person_id])
                        del removable_person_ids[person_id]
                        count_removed_people += 1
                        print(f"Successfully removed person {person_id} from the simulation.")
                    except traci.exceptions.TraCIException as e:
                        print(f"TraCI error while removing person {person_id}: {e}")
                    except Exception as e:
                        print(f"Unexpected error while removing person {person_id}: {e}")
                else:
                    print("No removable people found (all reserved or in taxis).")
                    break
        except Exception as e:
            print(f"Unexpected error during person removal: {e}")
        
        self.new_res_counter -= count_removed_people
        print(f"Finished attempting to remove {num_people} people.")


    def _remove_taxis(self, num_taxis):
        """
        Dynamically removes taxis from the simulation. Will only remove unoccupied and unassigned taxis or taxis that are out of commission

        Args:
        - num_taxis: The maximum number of taxis to try to remove from the simulation
        """
        print(f"Removing {num_taxis} taxis dynamically...")
        # for _ in range(num_taxis):
        #     active_taxi_ids = [tid for tid in traci.vehicle.getIDList() if tid in self.taxi_ids]
        #     if active_taxi_ids:
        #         taxi_id = active_taxi_ids.pop(0)
        #         try:
        #             traci.vehicle.remove(taxi_id)
        #             self.taxi_ids.remove(taxi_id)
        #             print(f"Removed taxi {taxi_id}")
        #         except traci.exceptions.TraCIException as e:
        #             print(f"Error removing taxi {taxi_id}: {e}")
        #     else:
        #         print("No more taxis to remove.")
        #         break

        # List of removable taxis: only remove taxis that are unassigned or out of commission
        removable_taxis = [*self.out_of_commission.keys(), *self.empty_taxis.keys()]
        
        for _ in range(num_taxis):
            if removable_taxis:
                taxi_id = removable_taxis.pop(0)
                try:
                    self.taxi_ids.remove(taxi_id)
                    if taxi_id in self.empty_taxis.keys():
                        traci.vehicle.remove(taxi_id)
                        del self.empty_taxis[taxi_id]
                    else:
                        del self.out_of_commission[taxi_id]
                except traci.exceptions.TraCIException as e:
                    print(f"Error removing taxi {taxi_id}: {e}")
            else:
                print("No more taxis to remove.")
                break

    def _remove_chargers(self, num_chargers):
        """
        Dynamically deactivates chargers in the simulation. Induction loops (detectors) cannot be removed dynamically through TraCI,
        so old chargers will still show up in the simulation

        Args:
        - num_chargers: The maximum number of chargers to deactivate
        """
        print(f"Removing {num_chargers} chargers dynamically...")
        for _ in range(num_chargers):
            if self.active_chargers:
                charger_id, lane_id, position = self.active_chargers.pop(0)
                print(f"Removed charger {charger_id} from lane {lane_id} at position {position}.")
            else:
                print("No more chargers to remove.")
                break

  
    def get_electricity_consumption(self):
        """
        Returns a dictionary of taxi_id -> cumulative electricity consumption in Wh. 
        """
        consumption = self.electricity_consumption_per_taxi
        consumption["time"] = traci.simulation.getTime()
        return consumption

    def get_vehicle_positions(self):
        """
        Returns the positions of all taxis as a dictionary:
        {
        "taxi_0": {"lat": <latitude>, "lon": <longitude>},
        "taxi_1": {"lat": <latitude>, "lon": <longitude>},
        ...
        }
        """
        vehicle_positions = {}
        for taxi_id in self.taxi_ids:
            if taxi_id in traci.vehicle.getIDList():
                try:
                    x, y = traci.vehicle.getPosition(taxi_id)
                    # Convert SUMO coordinates (x, y) to geo-coordinates (lon, lat)
                    lon, lat = traci.simulation.convertGeo(x, y)
                    vehicle_positions[taxi_id] = {'lat': lat, 'lon': lon}
                except traci.exceptions.TraCIException as e:
                    print(f"Error getting position for taxi {taxi_id}: {e}")
        return vehicle_positions

    def get_passenger_positions(self):
        """
        Returns the positions of all passengers in the simulation as a dictionary:
        {
        "person_0": {"lat": <latitude>, "lon": <longitude>},
        "person_1": {"lat": <latitude>, "lon": <longitude>},
        ...
        }
        """
        passenger_positions = {}
        for person_id in traci.person.getIDList():
            # We only consider persons that we have tracked
            if person_id in self.person_ids:
                try:
                    x, y = traci.person.getPosition(person_id)
                    lon, lat = traci.simulation.convertGeo(x, y)
                    passenger_positions[person_id] = {'lat': lat, 'lon': lon}
                except traci.exceptions.TraCIException as e:
                    print(f"Error getting position for passenger {person_id}: {e}")
        return passenger_positions
    
    def get_charger_positions(self):
        """
        Returns the positions of all chargers in the simulation as a dictionary:
        {
        "charger_0": {"lat": <latitude>, "lon": <longitude>},
        "charger_1": {"lat": <latitude>, "lon": <longitude>},
        ...
        }
        """
        charger_positions = {}
        for charger_id, lane_id, position in self.active_chargers:
            try:
                lane = self.net.getLane(lane_id)
                # Use lane.getCoord() to get the (x, y) coordinate at the given position
                x, y = lane.getCoord(position)
                lon, lat = traci.simulation.convertGeo(x, y)
                charger_positions[charger_id] = {'lat': lat, 'lon': lon}
            except Exception as e:
                print(f"Error getting position for charger {charger_id}: {e}")
        return charger_positions

    def getXYFromLanePos(self, lane, lane_pos):
        """
        Returns the (x, y) coordinate at a specific position along the lane.
        lane: sumolib.net.Lane object
        lane_pos: distance along the lane from the start (0) to lane length
        """
        shape = lane.getShape()  # list of (x, y) tuples along the lane
        
        # If lane_pos is 0 or very small, just return the first point
        if lane_pos <= 0:
            return shape[0]
        
        dist_left = lane_pos
        x0, y0 = shape[0]
        for i in range(1, len(shape)):
            x1, y1 = shape[i]
            segment_length = math.sqrt((x1 - x0)**2 + (y1 - y0)**2)
            
            if segment_length >= dist_left:
                # The position falls within this segment
                ratio = dist_left / segment_length
                x = x0 + ratio * (x1 - x0)
                y = y0 + ratio * (y1 - y0)
                return x, y
            
            # Move to the next segment
            dist_left -= segment_length
            x0, y0 = x1, y1
        
        # If lane_pos is beyond lane length, return the last shape point
        return shape[-1]

    def get_charger_positions(self):
        """
        Returns the positions of all chargers in the simulation as a dictionary:
        {
          "charger_0": {"lat": <latitude>, "lon": <longitude>},
          "charger_1": {"lat": <latitude>, "lon": <longitude>},
          ...
        }
        """
        charger_positions = {}
        for charger_id, lane_id, position in self.active_chargers:
            try:
                lane = self.net.getLane(lane_id)
                # Use the helper function to get (x, y) at the given position
                x, y = self.getXYFromLanePos(lane, position)
                lon, lat = traci.simulation.convertGeo(x, y)
                charger_positions[charger_id] = {'lat': lat, 'lon': lon}
            except Exception as e:
                print(f"Error getting position for charger {charger_id}: {e}")
        return charger_positions

    def get_battery_levels(self):
        """
        Returns a dictionary of taxi_id -> battery level (in percentage).
        """
        battery_levels = {}
        for taxi_id in self.taxi_ids:
            if taxi_id in traci.vehicle.getIDList():
                try:
                    actual_capacity = float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity"))
                    maximum_capacity = float(traci.vehicle.getParameter(taxi_id, "device.battery.maximumBatteryCapacity"))
                    soc_percentage = (actual_capacity / maximum_capacity) * 100.0  # Calculate State of Charge (SoC) in percentage
                    battery_levels[taxi_id] = soc_percentage
                except traci.exceptions.TraCIException as e:
                    print(f"Error getting battery level for taxi {taxi_id}: {e}")
        return battery_levels
    
    def get_average_passenger_wait_time(self):
        """
        Returns the average passenger wait time in seconds.
        If no reservations have been picked up, returns 0.
        """
        if len(self.reservation_wait_times.values()) > 0:
            return sum(self.reservation_wait_times.values()) / len(self.reservation_wait_times.values())
        else:
            return 0.0

    def get_active_passengers_count(self):
        """
        Returns the number of active passengers.
        Active passengers are those who are waiting to be picked up, assigned and waiting,
        or currently riding in a taxi.
        """
        # waiting_reservations: reservations waiting to be picked up
        # assigned_reservations: reservations assigned to a taxi but not picked up yet
        # heading_home_reservations: reservations currently in a taxi (picked up and not dropped off yet)
        active_passengers = len(self.waiting_reservations) + len(self.assigned_reservations) + len(self.heading_home_reservations)
        output = {"active_passengers": active_passengers, "time": traci.simulation.getTime()}
        return output
    
    def get_active_chargers_count(self):
        """
        Returns a dictionary with:
        - active_chargers: the number of chargers currently in use
        - time: the current simulation time from TraCI
        """
        current_time = traci.simulation.getTime()
        active_count = 0
        distance_threshold = 5.0  # meters within which a taxi is considered to be using the charger

        # Get the list of taxis currently in the simulation
        taxis_in_sim = traci.vehicle.getIDList()

        for charger_id, charger_lane_id, charger_position in self.active_chargers:
            # Check if any taxi is near this charger
            charger_in_use = False
            for taxi_id in taxis_in_sim:
                try:
                    taxi_lane = traci.vehicle.getLaneID(taxi_id)
                    taxi_position = traci.vehicle.getLanePosition(taxi_id)
                    if taxi_lane == charger_lane_id and abs(taxi_position - charger_position) < distance_threshold:
                        charger_in_use = True
                        break
                except traci.exceptions.TraCIException:
                    # If there's an error fetching parameters for a taxi, ignore and continue
                    pass

            if charger_in_use:
                active_count += 1

        return {"active_chargers": active_count, "time": current_time}
    
    def get_taxis_with_passengers_count(self):
        """
        Returns the number of taxis currently carrying passengers.
        A taxi is considered to have passengers if it is in the self.heading_home_reservations dict (actually transporting a passenger).
        Also, any taxi in dropping_off_taxis is already included since they're moving a passenger.
        """
        # Each taxi that is in dropping_off_taxis has a passenger.
        # heading_home_reservations maps reservation_id -> taxi_id for those currently with passengers.
        # dropping_off_taxis keys are also taxi_ids currently transporting passengers.

        # The number of unique taxi IDs in self.heading_home_reservations.values() or in self.dropping_off_taxis keys 
        # will give us the count of taxis with passengers.
        
        taxis_with_passengers = set()

        for taxi_id in self.dropping_off_taxis.keys():
            taxis_with_passengers.add(taxi_id)

        # heading_home_reservations also indicates that the reservation is currently being delivered by that taxi
        for taxi_id in self.heading_home_reservations.values():
            taxis_with_passengers.add(taxi_id)

        current_time = traci.simulation.getTime()
        return {"taxis_with_passengers": len(taxis_with_passengers), "time": current_time}

    def get_passenger_unsatisfaction_rate(self):
        """
        Calculate the passenger unsatisfaction rate.
        Unsatisfaction rate = (Number of people waiting more than 15 minutes) / (Total people who have started)

        - A person "starts" their reservation at their depart_time.
        - A person is considered unsatisfied if:
        current_time - depart_time > 900 seconds (15 minutes) AND not picked up yet (still waiting or assigned).
        """
        current_time = traci.simulation.getTime()

        # Total people who have started = all reservations whose depart_time <= current_time
        total_started = 0
        unsatisfied_count = 0

        for res_id, res_data in self.all_valid_res.items():
            depart_time = res_data[5]

            if depart_time <= current_time:
                total_started += 1
                wait_duration = current_time - depart_time

                # Check if this reservation is not picked up yet
                # Not picked up means reservation_id is in waiting_reservations or assigned_reservations
                # If picked up -> would be in heading_home_reservations or completed_reservations
                not_picked_up = (res_id in self.waiting_reservations) or (res_id in self.assigned_reservations)

                if not_picked_up and wait_duration > 900:
                    unsatisfied_count += 1

        unsatisfied_rate = (unsatisfied_count / total_started) if total_started > 0 else 0.0

        return {
            "unsatisfied_rate": unsatisfied_rate,
            "unsatisfied_count": unsatisfied_count,
            "total_started": total_started,
            "time": current_time
        }
    
    def get_total_earnings(self):
        """
        Computes total earnings from all completed reservations.
        Uses self.completed_reservations_by_taxi and the pricing model described.
        """
        total_satisfied_reservations = 0
        total_earnings = 0
        # Iterate over all taxi IDs and sum up the earnings
        for taxi_id in self.taxi_ids:
            taxi_earnings = 0
            if taxi_id in self.completed_reservations_by_taxi.keys():
                total_satisfied_reservations += len(self.completed_reservations_by_taxi[taxi_id])
                for completed_trip in self.completed_reservations_by_taxi[taxi_id]:
                    trip_length = completed_trip[0] / 1000  
                    trip_demand_multiplier = completed_trip[1]
                    trip_tod_rate = completed_trip[2]
                    trip_earnings = self.taxi_ride_base_price + (
                                (trip_length * self.taxi_ride_distance_rate) * trip_demand_multiplier * trip_tod_rate)
                    taxi_earnings += trip_earnings
            total_earnings += taxi_earnings
        return total_earnings

    def get_total_cost(self):
        """
        Computes total cost from all charging trips and tows.
        Uses self.cost_per_charging_trip and self.cost_per_tow.
        """
        total_cost = 0.0
        total_num_charging_trips = 0
        total_num_tows = 0

        # Iterate over all taxi IDs and sum up the costs
        for taxi_id in self.taxi_ids:
            if taxi_id in self.cost_per_charging_trip.keys():
                total_num_charging_trips += len(self.cost_per_charging_trip[taxi_id])
                total_cost += (len(self.cost_per_charging_trip[taxi_id]) * self.charge_base_price) + sum(self.cost_per_charging_trip[taxi_id])
            if taxi_id in self.cost_per_tow.keys():
                total_num_tows += len(self.cost_per_tow[taxi_id])
                total_cost += (len(self.cost_per_tow[taxi_id]) * self.tow_base_price) + sum(self.cost_per_tow[taxi_id])

        return total_cost

    def get_profit(self):
        """
        Computes profit as total earnings - total cost.
        """
        total_earnings = self.get_total_earnings()
        total_cost = self.get_total_cost()
        # Profit = Earnings - Cost
        return total_earnings - total_cost