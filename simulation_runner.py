# import threading
# import traci
# import sumolib
# import random

# class SimulationRunner(threading.Thread):
#     def __init__(self, step_length, sim_length, num_people=3, num_taxis=3):
#         super().__init__()
#         self.step_length = step_length
#         self.sim_length = sim_length
#         self.num_people = num_people
#         self.num_taxis = num_taxis
#         self.network_file = "downtown_houston.net.xml"
#         self.sumo_cfg = "simulation2.sumocfg"
#         self.net = sumolib.net.readNet(self.network_file)
#         self.taxi_ids = []
#         self.person_ids = []
#         self.charger_ids = []
#         self.active_chargers = []
#         self.stop_event = threading.Event()
#         self.lock = threading.Lock()  # For thread safety
#         self.is_running = False

#     def run(self):
#         self.is_running = True
#         self.generate_persons_xml(num_people=self.num_people)
#         self.initialize_simulation()
#         self.spawn_taxis(num_taxis=self.num_taxis)
#         timestep = 0

#         try:
#             while not self.stop_event.is_set() and traci.simulation.getTime() < self.sim_length:
#                 traci.simulationStep()
#                 timestep += 1

#                 # Simulate charging behavior
#                 self.simulate_charging()

#                 # Simulate energy consumption
#                 self.simulate_energy_consumption()

#         except traci.exceptions.TraCIException as e:
#             print("TraCI encountered an error:", e)
#         finally:
#             traci.close()
#             self.is_running = False
#             print("Simulation ended.")

#     # ... (rest of the methods remain the same)


#     def generate_persons_xml(self, num_people=3):
#         """Generates an XML file with person definitions including ride stages."""
#         valid_edges = [edge.getID() for edge in self.net.getEdges() if edge.getLaneNumber() > 0]
#         persons = []
#         for i in range(num_people):
#             person_id = f"person_{i}"
#             self.person_ids.append(person_id)
#             pickup_edge = random.choice(valid_edges)
#             dropoff_edge = random.choice(valid_edges)
#             while pickup_edge == dropoff_edge:
#                 dropoff_edge = random.choice(valid_edges)
#             person_xml = f'''
#     <person id="{person_id}" depart="0.00">
#         <ride from="{pickup_edge}" to="{dropoff_edge}" lines="taxi"/>
#     </person>
#         '''
#             persons.append(person_xml)
#             print(f"Person {person_id} added with ride from {pickup_edge} to {dropoff_edge}")

#         with open('persons.add.xml', 'w') as f:
#             f.write('<additional>\n')
#             for person in persons:
#                 f.write(person)
#             f.write('</additional>\n')
#         print("Persons written to 'persons.add.xml'")

#     def initialize_simulation(self):
#         """Initializes the SUMO simulation."""
#         sumo_binary = sumolib.checkBinary('sumo-gui')  # Use 'sumo' if you don't need the GUI
#         sumo_cmd = [
#             sumo_binary,
#             "-c", self.sumo_cfg,
#             "--start",
#             "--quit-on-end",
#             "--step-length", str(self.step_length),
#             "--additional-files", "vehicle_type.add.xml,persons.add.xml"
#         ]
#         traci.start(sumo_cmd)

#     def spawn_taxis(self, num_taxis=3):
#         """Spawns taxis at valid edges."""
#         valid_edges = [edge.getID() for edge in self.net.getEdges() if edge.getLaneNumber() > 0]
#         for i in range(num_taxis):
#             taxi_id = f"taxi_{len(self.taxi_ids)}"
#             start_edge = random.choice(valid_edges)
#             traci.route.add(f"route_{taxi_id}", [start_edge])
#             traci.vehicle.add(taxi_id, routeID=f"route_{taxi_id}", typeID="taxi")
#             # Initialize battery capacity
#             traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", "500")
#             print(f"Spawned taxi {taxi_id} at edge {start_edge}")
#             self.taxi_ids.append(taxi_id)

#     def spawn_taxis_at_runtime(self, num_taxis):
#         """Adds taxis during simulation runtime."""
#         if not self.is_running:
#             print("Simulation not running. Cannot add taxis.")
#             return
#         with self.lock:
#             valid_edges = [edge.getID() for edge in self.net.getEdges() if edge.getLaneNumber() > 0]
#             for i in range(num_taxis):
#                 taxi_id = f"taxi_{len(self.taxi_ids)}"
#                 start_edge = random.choice(valid_edges)
#                 traci.route.add(f"route_{taxi_id}", [start_edge])
#                 traci.vehicle.add(taxi_id, routeID=f"route_{taxi_id}", typeID="taxi")
#                 traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", "500")
#                 print(f"Spawned taxi {taxi_id} at edge {start_edge}")
#                 self.taxi_ids.append(taxi_id)

#     def add_people_at_runtime(self, num_people):
#         """Adds people dynamically during simulation runtime."""
#         if not self.is_running:
#             print("Simulation not running. Cannot add people.")
#             return
#         with self.lock:
#             valid_edges = [edge.getID() for edge in self.net.getEdges() if edge.getLaneNumber() > 0]
#             for i in range(num_people):
#                 person_id = f"person_{len(self.person_ids)}"
#                 self.person_ids.append(person_id)
#                 pickup_edge = random.choice(valid_edges)
#                 dropoff_edge = random.choice(valid_edges)
#                 while pickup_edge == dropoff_edge:
#                     dropoff_edge = random.choice(valid_edges)
#                 depart_time = traci.simulation.getTime()
#                 traci.person.add(person_id, edgeID=pickup_edge, pos=0, depart=depart_time)
#                 traci.person.appendDrivingStage(person_id, toEdge=dropoff_edge, lines="taxi")
#                 print(f"Added person {person_id} dynamically with ride from {pickup_edge} to {dropoff_edge}")

#     def add_chargers_at_runtime(self, num_chargers):
#         """Adds chargers dynamically during simulation runtime."""
#         if not self.is_running:
#             print("Simulation not running. Cannot add chargers.")
#             return
#         with self.lock:
#             valid_lanes = [lane for edge in self.net.getEdges() for lane in edge.getLanes()]
#             for i in range(num_chargers):
#                 charger_id = f"charger_{len(self.charger_ids)}"
#                 self.charger_ids.append(charger_id)
#                 lane = random.choice(valid_lanes)
#                 lane_id = lane.getID()
#                 position = random.uniform(0, lane.getLength())
#                 charger_info = (charger_id, lane_id, position)
#                 self.active_chargers.append(charger_info)
#                 print(f"Activated charger {charger_id} at lane {lane_id}, position {position}")

#     def simulate_charging(self):
#         """Simulates charging for taxis at charger locations."""
#         for taxi_id in self.taxi_ids:
#             try:
#                 taxi_lane = traci.vehicle.getLaneID(taxi_id)
#                 taxi_position = traci.vehicle.getLanePosition(taxi_id)
#                 for charger_id, charger_lane_id, charger_position in self.active_chargers:
#                     if taxi_lane == charger_lane_id and abs(taxi_position - charger_position) < 5:
#                         # Simulate charging by increasing battery capacity
#                         current_capacity = float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity"))
#                         maximum_capacity = float(traci.vehicle.getParameter(taxi_id, "device.battery.maximumBatteryCapacity"))
#                         new_capacity = min(current_capacity + 50, maximum_capacity)
#                         traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", str(new_capacity))
#                         print(f"Taxi {taxi_id} is charging at {charger_id}. New capacity: {new_capacity}")
#             except traci.exceptions.TraCIException as e:
#                 print(f"Error while simulating charging for taxi {taxi_id}: {e}")

#     def simulate_energy_consumption(self):
#         """Simulates energy consumption for taxis."""
#         for taxi_id in self.taxi_ids:
#             try:
#                 current_capacity = float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity"))
#                 consumption_rate = 1  # Units of battery capacity consumed per timestep
#                 new_capacity = max(current_capacity - consumption_rate, 0)
#                 traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", str(new_capacity))
#             except traci.exceptions.TraCIException as e:
#                 print(f"Error while simulating energy consumption for taxi {taxi_id}: {e}")

#     def stop(self):
#         """Stops the simulation."""
#         self.stop_event.set()


import threading
import traci
import sumolib
import random
from queue import Queue

class SimulationRunner(threading.Thread):
    def __init__(self, step_length, sim_length, num_people=3, num_taxis=3):
        super().__init__()
        self.step_length = step_length
        self.sim_length = sim_length
        self.num_people = num_people
        self.num_taxis = num_taxis
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

    def run(self):
        self.is_running = True
        self.generate_persons_xml(num_people=self.num_people)
        self.initialize_simulation()
        self.spawn_taxis(num_taxis=self.num_taxis)
        timestep = 0

        try:
            while not self.stop_event.is_set() and traci.simulation.getTime() < self.sim_length:
                traci.simulationStep()
                timestep += 1

                # Process commands from the queue
                while not self.command_queue.empty():
                    command = self.command_queue.get()
                    if command['action'] == 'add_person':
                        self._add_people(command['num_people'])
                    elif command['action'] == 'add_taxi':
                        self._spawn_taxis_at_runtime(command['num_taxis'])
                    elif command['action'] == 'add_charger':
                        self._add_chargers_at_runtime(command['num_chargers'])

                # Simulate charging behavior
                self.simulate_charging()
                self.simulate_energy_consumption()

        except traci.exceptions.TraCIException as e:
            print("TraCI encountered an error:", e)
        finally:
            traci.close()
            self.is_running = False
            print("Simulation ended.")

    def generate_persons_xml(self, num_people=3):
        """Generates an XML file with person definitions including ride stages."""
        valid_edges = [edge.getID() for edge in self.net.getEdges() if edge.getLaneNumber() > 0]
        persons = []
        for i in range(num_people):
            person_id = f"person_{i}"
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
            "--additional-files", "vehicle_type.add.xml,persons.add.xml"
        ]
        traci.start(sumo_cmd)

    def spawn_taxis(self, num_taxis=3):
        """Spawns initial taxis at valid edges."""
        valid_edges = [edge.getID() for edge in self.net.getEdges() if edge.getLaneNumber() > 0]
        for _ in range(num_taxis):
            taxi_id = f"taxi_{len(self.taxi_ids)}"
            self.taxi_ids.append(taxi_id)
            start_edge = random.choice(valid_edges)
            traci.route.add(f"route_{taxi_id}", [start_edge])
            traci.vehicle.add(taxi_id, routeID=f"route_{taxi_id}", typeID="taxi")
            traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", "500")
            print(f"Spawned taxi {taxi_id} at edge {start_edge}")

    def _spawn_taxis_at_runtime(self, num_taxis):
        """Adds taxis during simulation runtime."""
        valid_edges = [edge.getID() for edge in self.net.getEdges() if edge.getLaneNumber() > 0]
        for _ in range(num_taxis):
            taxi_id = f"taxi_{len(self.taxi_ids)}"
            self.taxi_ids.append(taxi_id)
            start_edge = random.choice(valid_edges)
            traci.route.add(f"route_{taxi_id}", [start_edge])
            traci.vehicle.add(taxi_id, routeID=f"route_{taxi_id}", typeID="taxi")
            traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", "500")
            print(f"Spawned taxi {taxi_id} at edge {start_edge}")

    def _add_people(self, num_people):
        """Adds people dynamically during simulation runtime."""
        valid_edges = [edge.getID() for edge in self.net.getEdges() if edge.getLaneNumber() > 0]
        for _ in range(num_people):
            person_id = f"person_{len(self.person_ids)}"
            self.person_ids.append(person_id)
            pickup_edge = random.choice(valid_edges)
            dropoff_edge = random.choice(valid_edges)
            while pickup_edge == dropoff_edge:
                dropoff_edge = random.choice(valid_edges)
            depart_time = traci.simulation.getTime() + self.step_length  # Ensure depart time is in the future
            traci.person.add(person_id, edgeID=pickup_edge, pos=0, depart=depart_time)
            traci.person.appendDrivingStage(person_id, toEdge=dropoff_edge, lines="taxi")
            print(f"Added person {person_id} dynamically with ride from {pickup_edge} to {dropoff_edge}")

    def _add_chargers_at_runtime(self, num_chargers):
        """Adds chargers dynamically during simulation runtime."""
        valid_lanes = [lane for edge in self.net.getEdges() for lane in edge.getLanes()]
        for _ in range(num_chargers):
            charger_id = f"charger_{len(self.charger_ids)}"
            self.charger_ids.append(charger_id)
            lane = random.choice(valid_lanes)
            lane_id = lane.getID()
            position = random.uniform(0, lane.getLength())
            charger_info = (charger_id, lane_id, position)
            self.active_chargers.append(charger_info)
            print(f"Activated charger {charger_id} at lane {lane_id}, position {position}")

    def simulate_charging(self):
        """Simulates charging for taxis at charger locations."""
        for taxi_id in self.taxi_ids:
            try:
                taxi_lane = traci.vehicle.getLaneID(taxi_id)
                taxi_position = traci.vehicle.getLanePosition(taxi_id)
                for charger_id, charger_lane_id, charger_position in self.active_chargers:
                    if taxi_lane == charger_lane_id and abs(taxi_position - charger_position) < 5:
                        # Simulate charging by increasing battery capacity
                        current_capacity = float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity"))
                        maximum_capacity = float(traci.vehicle.getParameter(taxi_id, "device.battery.maximumBatteryCapacity"))
                        new_capacity = min(current_capacity + 50, maximum_capacity)
                        traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", str(new_capacity))
                        print(f"Taxi {taxi_id} is charging at {charger_id}. New capacity: {new_capacity}")
            except traci.exceptions.TraCIException as e:
                print(f"Error while simulating charging for taxi {taxi_id}: {e}")

    def simulate_energy_consumption(self):
        """Simulates energy consumption for taxis."""
        for taxi_id in self.taxi_ids:
            try:
                current_capacity = float(traci.vehicle.getParameter(taxi_id, "device.battery.actualBatteryCapacity"))
                consumption_rate = 1  # Units of battery capacity consumed per timestep
                new_capacity = max(current_capacity - consumption_rate, 0)
                traci.vehicle.setParameter(taxi_id, "device.battery.actualBatteryCapacity", str(new_capacity))
            except traci.exceptions.TraCIException as e:
                print(f"Error while simulating energy consumption for taxi {taxi_id}: {e}")

    def stop(self):
        """Stops the simulation."""
        self.stop_event.set()
