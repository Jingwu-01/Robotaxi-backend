import threading
import traci
import sumolib
import random
from queue import Queue
import time
import traci.constants

class SimulationRunner(threading.Thread):
    def __init__(self, step_length, sim_length, num_people=3, num_taxis=3, num_chargers=0):
        super().__init__()
        self.step_length = step_length
        self.sim_length = sim_length
        self.num_people = num_people
        self.num_taxis = num_taxis
        self.num_chargers = num_chargers
        self.network_file = "downtown_houston.net.xml"
        self.sumo_cfg = "simulation2.sumocfg"
        self.net = sumolib.net.readNet(self.network_file)
        self.taxi_ids = []
        self.person_ids = []
        self.charger_ids = []
        self.active_chargers = []
        self.stop_event = threading.Event()
        self.is_running = False
        self.command_queue = Queue()

        # Counters for unique IDs
        self.taxi_counter = 0
        self.person_counter = 0
        self.charger_counter = 0

        # Status dictionary and lock for thread-safe access
        self.status = {}
        self.status_lock = threading.Lock()

        self.cumulative_consumption = {}
        self.cumulative_consumption_lock = threading.Lock()

        self.chargers_in_use = set()
        self.chargers_in_use_lock = threading.Lock()

        self.passenger_request_times = {}  # person_id -> request_time
        self.passenger_wait_times = []     # List of wait times
        self.passenger_wait_times_lock = threading.Lock()

        self.waiting_passengers = {}  # {person_id: start_waiting_time}
        self.waiting_passengers_lock = threading.Lock()

    def run(self):
        self.is_running = True
        self.generate_persons_xml(num_people=self.num_people)
        self.initialize_simulation()
        self.spawn_taxis(num_taxis=self.num_taxis)
        self.add_chargers(num_chargers=self.num_chargers)  # Add chargers at start
        timestep = 0

        try:
            while not self.stop_event.is_set() and traci.simulation.getTime() < self.sim_length:

                # Process commands before simulation step
                while not self.command_queue.empty():
                    command = self.command_queue.get()
                    try:
                        if command['action'] == 'add_person':
                            self._add_people(command['num_people'])
                        elif command['action'] == 'add_taxi':
                            self._spawn_taxis_at_runtime(command['num_taxis'])
                        elif command['action'] == 'add_charger':
                            self._add_chargers_at_runtime(command['num_chargers'])
                        elif command['action'] == 'remove_person':
                            self._remove_people(command['num_people'])
                        elif command['action'] == 'remove_taxi':
                            self._remove_taxis(command['num_taxis'])
                        elif command['action'] == 'remove_charger':
                            self._remove_chargers(command['num_chargers'])
                    except Exception as e:
                        print(f"Error processing command {command}: {e}")

                # Advance the simulation step
                traci.simulationStep()
                timestep += 1

                current_time = traci.simulation.getTime()
        
                # Update waiting passengers
                with self.waiting_passengers_lock:
                    current_person_ids = set(traci.person.getIDList())
                    # Remove passengers who are no longer in the simulation
                    removed_person_ids = set(self.waiting_passengers.keys()) - current_person_ids
                    for person_id in removed_person_ids:
                        del self.waiting_passengers[person_id]

                # Record request times for new passengers
                departed_persons = traci.simulation.getDepartedPersonIDList()
                current_time = traci.simulation.getTime()
                for person_id in departed_persons:
                    if person_id.startswith("person_"):
                        with self.passenger_wait_times_lock:
                            self.passenger_request_times[person_id] = current_time
                            print(f"Person {person_id} requested a ride at time {current_time}")

                # Check for passengers who have been picked up
                for person_id in list(self.passenger_request_times.keys()):
                    try:
                        vehicle_id = traci.person.getVehicle(person_id)
                        if vehicle_id != "":
                            # Passenger has been picked up
                            pickup_time = current_time
                            request_time = self.passenger_request_times.pop(person_id)
                            wait_time = pickup_time - request_time
                            with self.passenger_wait_times_lock:
                                self.passenger_wait_times.append(wait_time)
                                print(f"Person {person_id} picked up at time {pickup_time}, wait time: {wait_time} seconds")
                    except traci.exceptions.TraCIException as e:
                        # Handle cases where the person might have left the simulation
                        print(f"Error checking vehicle for person {person_id}: {e}")
                        with self.passenger_wait_times_lock:
                            self.passenger_request_times.pop(person_id, None)

                # Update status
                with self.status_lock:
                    simulation_time = traci.simulation.getTime()
                    num_taxis_simulation = len([vid for vid in traci.vehicle.getIDList() if vid.startswith("taxi_")])
                    num_people_simulation = len([pid for pid in traci.person.getIDList() if pid.startswith("person_")])
                    num_chargers_simulation = len(self.active_chargers)  # Assuming self.active_chargers is accurate

                    self.status = {
                        'simulation_time': simulation_time,
                        'num_taxis': num_taxis_simulation,
                        'num_people': num_people_simulation,
                        'num_chargers': num_chargers_simulation,
                    }

                # Simulate charging and energy consumption
                self.simulate_charging()
                self.simulate_energy_consumption()

                # Small sleep to prevent tight loop
                time.sleep(0.01)

        except traci.exceptions.FatalTraCIError as e:
            print(f"Fatal TraCI error: {e}")
            self.stop_event.set()
        except Exception as e:
            print(f"Unexpected error: {e}")
        finally:
            traci.close()
            self.is_running = False
            print("Simulation ended.")

    def generate_persons_xml(self, num_people=3):
        """Generates an XML file with person definitions including ride stages."""
        valid_edges = [edge.getID() for edge in self.net.getEdges() if edge.getLaneNumber() > 0]
        persons = []
        for _ in range(num_people):
            person_id = f"person_{self.person_counter}"
            self.person_counter += 1
            self.person_ids.append(person_id)
            pickup_edge = random.choice(valid_edges)
            dropoff_edge = random.choice(valid_edges)
            while pickup_edge == dropoff_edge:
                dropoff_edge = random.choice(valid_edges)
            person_xml = f'''
    <person id="{person_id}" depart="0.00">
        <ride from="{pickup_edge}" to="{dropoff_edge}" lines="taxi"/>
    </person>
            '''
            persons.append(person_xml)
            print(f"Person {person_id} added with ride from {pickup_edge} to {dropoff_edge}")

        with open('persons.add.xml', 'w') as f:
            f.write('<additional>\n')
            for person in persons:
                f.write(person)
            f.write('</additional>\n')
        print("Persons written to 'persons.add.xml'")

    def initialize_simulation(self):
        """Initializes the SUMO simulation."""
        sumo_binary = sumolib.checkBinary('sumo-gui')  # Use 'sumo' if you don't need the GUI
        sumo_cmd = [
            sumo_binary,
            "-c", self.sumo_cfg,
            "--start",
            "--quit-on-end",
            "--step-length", str(self.step_length),
            "--additional-files", "vehicle_type.add.xml,persons.add.xml",
            "--collision.action", "none",
            "--log", "sumo_log.txt",
            "--error-log", "sumo_error_log.txt",
            "--message-log", "sumo_message_log.txt",
            "--verbose", "true",

          
        ]
        traci.start(sumo_cmd)

    def spawn_taxis(self, num_taxis=3):
        """Spawns initial taxis with valid routes."""
        valid_edges = [edge.getID() for edge in self.net.getEdges() if edge.getLaneNumber() > 0]
        for _ in range(num_taxis):
            taxi_id = f"taxi_{self.taxi_counter}"
            self.taxi_counter += 1
            start_edge = random.choice(valid_edges)
            dest_edge = random.choice(valid_edges)
            while dest_edge == start_edge:
                dest_edge = random.choice(valid_edges)
            # Compute a valid route
            route = traci.simulation.findRoute(start_edge, dest_edge)
            if not route.edges:
                print(f"No valid route found for taxi {taxi_id} from {start_edge} to {dest_edge}. Skipping taxi.")
                continue
            route_edges = route.edges
            route_id = f"route_{taxi_id}"
            try:
                traci.route.add(route_id, route_edges)
                traci.vehicle.add(
                    taxi_id,
                    routeID=route_id,
                    typeID="taxi",  # Use your taxi type ID
                    departPos="random",
                    departLane="best",
                    departSpeed="max"
                )
                self.taxi_ids.append(taxi_id)
                print(f"Spawned taxi {taxi_id} with route from {start_edge} to {dest_edge}")
            except traci.exceptions.TraCIException as e:
                print(f"Error adding taxi {taxi_id}: {e}")

    def add_chargers(self, num_chargers=0):
        """Adds chargers at the start of the simulation."""
        valid_lanes = [lane for edge in self.net.getEdges() for lane in edge.getLanes()]
        for _ in range(num_chargers):
            charger_id = f"charger_{self.charger_counter}"
            self.charger_counter += 1
            self.charger_ids.append(charger_id)
            lane = random.choice(valid_lanes)
            lane_id = lane.getID()
            position = random.uniform(0, lane.getLength())
            charger_info = (charger_id, lane_id, position)
            self.active_chargers.append(charger_info)
            print(f"Activated charger {charger_id} at lane {lane_id}, position {position}")

    def _spawn_taxis_at_runtime(self, num_taxis):
        """Adds taxis during simulation runtime with valid routes."""
        valid_edges = [edge.getID() for edge in self.net.getEdges() if edge.getLaneNumber() > 0]
        for _ in range(num_taxis):
            taxi_id = f"taxi_{self.taxi_counter}"
            self.taxi_counter += 1
            start_edge = random.choice(valid_edges)
            dest_edge = random.choice(valid_edges)
            while dest_edge == start_edge:
                dest_edge = random.choice(valid_edges)
            # Compute a valid route
            route = traci.simulation.findRoute(start_edge, dest_edge)
            if not route.edges:
                print(f"No valid route found for taxi {taxi_id} from {start_edge} to {dest_edge}. Skipping taxi.")
                continue
            route_edges = route.edges
            route_id = f"route_{taxi_id}"
            try:
                traci.route.add(route_id, route_edges)
                traci.vehicle.add(
                    taxi_id,
                    routeID=route_id,
                    typeID="taxi",
                    departPos="random",
                    departLane="best",
                    departSpeed="max"
                )
                self.taxi_ids.append(taxi_id)
                print(f"Spawned taxi {taxi_id} with route from {start_edge} to {dest_edge}")
            except traci.exceptions.TraCIException as e:
                print(f"Error adding taxi {taxi_id}: {e}")

    def _add_people(self, num_people):
        """Adds people dynamically during simulation runtime."""
        valid_edges = [edge.getID() for edge in self.net.getEdges() if edge.getLaneNumber() > 0]
        for _ in range(num_people):
            person_id = f"person_{self.person_counter}"
            self.person_counter += 1
            pickup_edge = random.choice(valid_edges)
            dropoff_edge = random.choice(valid_edges)
            while pickup_edge == dropoff_edge:
                dropoff_edge = random.choice(valid_edges)
            depart_time = traci.simulation.getTime() + self.step_length  # Ensure depart time is in the future
            try:
                traci.person.add(person_id, edgeID=pickup_edge, pos=0, depart=depart_time)
                traci.person.appendDrivingStage(person_id, toEdge=dropoff_edge, lines="taxi")
                print(f"Added person {person_id} dynamically with ride from {pickup_edge} to {dropoff_edge}")
                with self.waiting_passengers_lock:
                    self.waiting_passengers[person_id] = depart_time  # Record their start waiting time
            except traci.exceptions.TraCIException as e:
                print(f"Error adding person {person_id}: {e}")

    def _add_chargers_at_runtime(self, num_chargers):
        """Adds chargers dynamically during simulation runtime."""
        valid_lanes = [lane for edge in self.net.getEdges() for lane in edge.getLanes()]
        for _ in range(num_chargers):
            charger_id = f"charger_{self.charger_counter}"
            self.charger_counter += 1
            lane = random.choice(valid_lanes)
            lane_id = lane.getID()
            position = random.uniform(0, lane.getLength())
            charger_info = (charger_id, lane_id, position)
            self.active_chargers.append(charger_info)
            print(f"Activated charger {charger_id} at lane {lane_id}, position {position}")

    def _remove_people(self, num_people):
        """Removes people from the simulation."""
        for _ in range(num_people):
            person_ids_in_simulation = [pid for pid in traci.person.getIDList() if pid.startswith("person_")]
            if person_ids_in_simulation:
                person_id = person_ids_in_simulation[0]
                try:
                    traci.person.remove(person_id)
                    print(f"Removed person {person_id} from the simulation.")
                except traci.exceptions.TraCIException as e:
                    print(f"Error removing person {person_id}: {e}")
            else:
                print("No more people to remove.")
                break

    def _remove_taxis(self, num_taxis):
        """Removes taxis from the simulation."""
        for _ in range(num_taxis):
            taxi_ids_in_simulation = [vid for vid in traci.vehicle.getIDList() if vid.startswith("taxi_")]
            if taxi_ids_in_simulation:
                taxi_id = taxi_ids_in_simulation[0]
                try:
                    traci.vehicle.remove(taxi_id)
                    print(f"Removed taxi {taxi_id} from the simulation.")
                except traci.exceptions.TraCIException as e:
                    print(f"Error removing taxi {taxi_id}: {e}")
            else:
                print("No more taxis to remove.")
                break

    def _remove_chargers(self, num_chargers):
        """Removes chargers from the simulation."""
        for _ in range(num_chargers):
            if self.active_chargers:
                charger_info = self.active_chargers.pop()
                charger_id = charger_info[0]
                print(f"Removed charger {charger_id} from the simulation.")
            else:
                print("No more chargers to remove.")
                break

    # def simulate_charging(self):
    #     """Simulates charging for taxis at charger locations."""
    #     for taxi_id in list(self.taxi_ids):
    #         if taxi_id not in traci.vehicle.getIDList():
    #             self.taxi_ids.remove(taxi_id)
    #             continue
    #         try:
    #             taxi_lane = traci.vehicle.getLaneID(taxi_id)
    #             taxi_position = traci.vehicle.getLanePosition(taxi_id)
    #             for charger_id, charger_lane_id, charger_position in self.active_chargers:
    #                 if taxi_lane == charger_lane_id and abs(taxi_position - charger_position) < 5:
    #                     # Simulate charging by increasing battery capacity
    #                     current_capacity = float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity"))
    #                     maximum_capacity = float(traci.vehicle.getParameter(taxi_id, "device.battery.maximumBatteryCapacity"))
    #                     new_capacity = min(current_capacity + 50, maximum_capacity)
    #                     traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", str(new_capacity))
    #         except traci.exceptions.TraCIException:
    #             pass  # Silently ignore or handle error
    
    def simulate_charging(self):
        with self.chargers_in_use_lock:
            # Clear the set of chargers in use
            self.chargers_in_use.clear()

            for taxi_id in self.taxi_ids:
                if taxi_id in traci.vehicle.getIDList():
                    try:
                        taxi_lane = traci.vehicle.getLaneID(taxi_id)
                        taxi_position = traci.vehicle.getLanePosition(taxi_id)
                        for charger_id, charger_lane_id, charger_position in self.active_chargers:
                            if taxi_lane == charger_lane_id and abs(taxi_position - charger_position) < 5:
                                # Add charger to the set of chargers in use
                                self.chargers_in_use.add(charger_id)

                                # Simulate charging by increasing battery capacity
                                current_soc = float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity"))
                                maximum_capacity = float(traci.vehicle.getParameter(taxi_id, "device.battery.maximumBatteryCapacity"))
                                # Charging rate in kWh per hour
                                charging_rate_kW = 50.0  # Adjust as needed
                                # Calculate energy charged during the timestep (in kWh)
                                delta_charge_kWh = charging_rate_kW * (self.step_length / 3600.0)
                                new_soc = min(current_soc + delta_charge_kWh, maximum_capacity)
                                traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", str(new_soc))
                    except traci.exceptions.TraCIException as e:
                        print(f"Error during charging simulation for vehicle {taxi_id}: {e}")


    def get_active_chargers_count(self):
        """Returns the number of chargers currently being used."""
        with self.chargers_in_use_lock:
            return len(self.chargers_in_use)


    def simulate_energy_consumption(self):
        with self.cumulative_consumption_lock:
            for vid in traci.vehicle.getIDList():
                try:
                    power_W = traci.vehicle.getElectricityConsumption(vid)
                    delta_energy_J = power_W * self.step_length 
                    delta_energy_J = max(delta_energy_J, 0.0)
                    self.cumulative_consumption[vid] = self.cumulative_consumption.get(vid, 0.0) + delta_energy_J
                except traci.exceptions.TraCIException as e:
                    # Handle non-electric vehicles or errors
                    print(f"Error getting electricity consumption for vehicle {vid}: {e}")
    
    def get_electricity_consumption(self):
        """Returns the cumulative electricity consumption of each taxi."""
        vehicle_consumption = {}
        with self.cumulative_consumption_lock:
            for vid, energy_J in self.cumulative_consumption.items():
                vehicle_consumption[vid] = energy_J
        return vehicle_consumption
    
    def get_active_passengers_count(self):
        """Returns the number of active passengers."""
        with self.status_lock:
            return self.status.get('num_people', 0)

    def get_taxis_with_passengers_count(self):
        """Returns the number of taxis currently carrying passengers."""
        count = 0
        for taxi_id in self.taxi_ids:
            if taxi_id in traci.vehicle.getIDList():
                try:
                    taxi_state = traci.vehicle.getParameter(taxi_id, "device.taxi.state")
                    if taxi_state == "occupied":
                        count += 1
                except traci.exceptions.TraCIException as e:
                    print(f"Error getting taxi state for taxi {taxi_id}: {e}")
        return count

    def get_average_wait_time(self):
        """Calculates and returns the average passenger wait time."""
        with self.passenger_wait_times_lock:
            if self.passenger_wait_times:
                average_wait_time = sum(self.passenger_wait_times) / len(self.passenger_wait_times)
                return average_wait_time
            else:
                return 0.0
            
    def get_status(self):
        """Returns the current status of the simulation."""
        with self.status_lock:
            return self.status.copy()

    def get_battery_levels(self):
        """Returns the current battery levels of all taxis."""
        battery_levels = {}
        for taxi_id in self.taxi_ids:
            if taxi_id in traci.vehicle.getIDList():
                try:
                    actual_capacity = float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity"))
                    maximum_capacity = float(traci.vehicle.getParameter(taxi_id, "device.battery.maximumBatteryCapacity"))
                    soc_percentage = (actual_capacity / maximum_capacity) * 100  # Calculate State of Charge (SoC) in percentage
                    battery_levels[taxi_id] = soc_percentage
                except traci.exceptions.TraCIException as e:
                    print(f"Error getting battery level for taxi {taxi_id}: {e}")
        return battery_levels
    
    def get_unsatisfied_passengers_percentage(self):
        """Calculates and returns the percentage of unsatisfied passengers."""
        with self.waiting_passengers_lock:
            total_waiting_passengers = len(self.waiting_passengers)
            if total_waiting_passengers == 0:
                return 0.0
            unsatisfied_count = 0
            current_time = traci.simulation.getTime()
            for person_id, start_time in self.waiting_passengers.items():
                waiting_time = current_time - start_time
                if waiting_time > 15 * 60:  # 15 minutes in seconds
                    unsatisfied_count += 1
            unsatisfaction_percentage = (unsatisfied_count / total_waiting_passengers) * 100
            return unsatisfaction_percentage

    def stop(self):
        """Stops the simulation."""
        self.stop_event.set()
