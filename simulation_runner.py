import threading
import traci
import sumolib
import random
from queue import Queue
import time


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
        self.net = None
        self.valid_edges = []
        self.command_queue = Queue()
        self.stop_event = threading.Event()
        self.is_running = False
        self.person_ids = []
        self.taxi_ids = []
        self.active_chargers = []
        self.assignments = {}

        # Counters
        self.person_counter = 0
        self.taxi_counter = 0
        self.charger_counter = 0

    def run(self):
        self.is_running = True
        try:
            self.initialize_network()
            self.ensure_all_routes_valid()
            self.initialize_simulation()
            self.spawn_taxis()
            self.add_chargers()
            self.simulation_loop()
        except Exception as e:
            print(f"Error during simulation: {e}")
        finally:
            self.cleanup()

    def initialize_network(self):
        print("Initializing network and filtering valid edges...")
        self.net = sumolib.net.readNet(self.network_file)
        self.valid_edges = [
            edge.getID()
            for edge in self.net.getEdges()
            if edge.getLaneNumber() > 0 and edge.getOutgoing() and edge.getIncoming()
        ]
        print(f"Valid edges: {len(self.valid_edges)}")

    def ensure_all_routes_valid(self):
        print("Validating all routes before starting the simulation...")
        self.generate_persons_xml()

    def generate_persons_xml(self):
        print("Generating person routes...")
        persons = []
        for _ in range(self.num_people):
            attempts = 0
            while attempts < 100:
                start_edge = random.choice(self.valid_edges)
                end_edge = random.choice(self.valid_edges)
                if start_edge != end_edge and self.validate_route(start_edge, end_edge):
                    person_id = f"person_{self.person_counter}"
                    self.person_counter += 1
                    self.person_ids.append(person_id)
                    persons.append(f'''
<person id="{person_id}" depart="0.00">
    <ride from="{start_edge}" to="{end_edge}" lines="taxi"/>
</person>
                    ''')
                    break
                attempts += 1
            else:
                print("No valid route found for a person after 100 attempts. Skipping.")
        with open('persons.add.xml', 'w') as f:
            f.write('<additional>\n')
            f.writelines(persons)
            f.write('</additional>\n')
        print(f"Validated and wrote {len(persons)} person routes to 'persons.add.xml'.")

    def validate_route(self, start_edge, end_edge):
        try:
            edge_from = self.net.getEdge(start_edge)
            edge_to = self.net.getEdge(end_edge)
            path, _ = self.net.getShortestPath(edge_from, edge_to, vClass="passenger")
            return path is not None and len(path) > 0
        except Exception as e:
            print(f"Route validation failed: {e}")
            return False

    def initialize_simulation(self):
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
            "vehicle_type.add.xml,persons.add.xml",
            "--collision.action",
            "none",
        ]
        traci.start(sumo_cmd)
        print("SUMO simulation started.")

    def get_status(self):
        """Fetch the current simulation status."""
        try:
            simulation_time = traci.simulation.getTime()
            num_taxis = len([tid for tid in traci.vehicle.getIDList() if tid in self.taxi_ids])
            num_people = len([pid for pid in traci.person.getIDList() if pid in self.person_ids])
            num_chargers = len(self.active_chargers)

            return {
                "simulation_time": simulation_time,
                "num_taxis": num_taxis,
                "num_people": num_people,
                "num_chargers": num_chargers,
            }
        except Exception as e:
            print(f"Error fetching simulation status: {e}")
            return {}


    def spawn_taxis(self):
        print("Spawning taxis...")
        for _ in range(self.num_taxis):
            start_edge = random.choice(self.valid_edges)
            try:
                taxi_id = f"taxi_{self.taxi_counter}"
                self.taxi_counter += 1
                traci.route.add(f"route_{taxi_id}", [start_edge])
                traci.vehicle.add(
                    taxi_id,
                    routeID=f"route_{taxi_id}",
                    typeID="taxi",
                    departPos="random",
                    departLane="best",
                    departSpeed="max",
                )
                self.taxi_ids.append(taxi_id)
                print(f"Spawned taxi {taxi_id} at edge {start_edge}")
            except traci.exceptions.TraCIException as e:
                print(f"Error spawning taxi: {e}")

    def add_chargers(self):
        print("Adding chargers...")
        for _ in range(self.num_chargers):
            edge_id = random.choice(self.valid_edges)
            lane = random.choice(self.net.getEdge(edge_id).getLanes())
            position = random.uniform(0, lane.getLength())
            charger_id = f"charger_{self.charger_counter}"
            self.charger_counter += 1
            self.active_chargers.append((charger_id, lane.getID(), position))
        print(f"Added {self.num_chargers} chargers.")

    def assign_taxis_to_reservations(self):
        reservations = traci.person.getTaxiReservations(0)
        for reservation in reservations:
            if reservation.id not in [res.id for res in self.assignments.values()]:
                for taxi_id in self.taxi_ids:
                    if taxi_id not in self.assignments:
                        try:
                            traci.vehicle.dispatchTaxi(taxi_id, reservation.id)
                            self.assignments[taxi_id] = reservation
                            print(f"Dispatched {taxi_id} to reservation {reservation.id}")
                        except traci.exceptions.TraCIException as e:
                            print(f"Skipping {taxi_id} due to route error: {e}")
                        break

    def monitor_pickups_and_dropoffs(self):
        for taxi_id, reservation in list(self.assignments.items()):
            person_id = reservation.persons[0]
            if person_id in traci.person.getIDList():
                current_vehicle = traci.person.getVehicle(person_id)
                if current_vehicle == taxi_id:
                    print(f"Person {person_id} is inside taxi {taxi_id}.")
                else:
                    print(f"Person {person_id} is waiting for taxi {taxi_id}.")
            else:
                print(f"Person {person_id} has been dropped off by taxi {taxi_id}.")
                self.assignments.pop(taxi_id)

    def simulation_loop(self):
        print("Simulation loop started.")
        timestep = 0

        while not self.stop_event.is_set() and traci.simulation.getTime() < self.sim_length:
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

                # Handle taxi assignments and monitor pickup/dropoff
                self.assign_taxis_to_reservations()
                self.monitor_pickups_and_dropoffs()

                # Increment the timestep
                timestep += 1

                # Optional: Add a short delay to prevent overloading
                time.sleep(0.01)

            except traci.exceptions.TraCIException as e:
                print(f"TraCI error during simulation loop at timestep {timestep}: {e}")
                break
            except Exception as e:
                print(f"Unexpected error during simulation loop at timestep {timestep}: {e}")
                break

        print("Exiting simulation loop.")



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
            print("Simulation cleanup complete.")


    def _add_people(self, num_people):
        print(f"Adding {num_people} people dynamically...")
        for _ in range(num_people):
            start_edge = random.choice(self.valid_edges)
            end_edge = random.choice(self.valid_edges)
            if start_edge != end_edge:
                person_id = f"person_dyn_{self.person_counter}"
                self.person_counter += 1
                self.person_ids.append(person_id)
                traci.person.add(person_id, start_edge, pos=0, depart=traci.simulation.getTime() + self.step_length)
                traci.person.appendDrivingStage(person_id, toEdge=end_edge, lines="taxi")
                print(f"Dynamically added person {person_id} from {start_edge} to {end_edge}")

    def _add_chargers_at_runtime(self, num_chargers):
        print(f"Adding {num_chargers} chargers dynamically...")
        for _ in range(num_chargers):
            edge_id = random.choice(self.valid_edges)
            lane = random.choice(self.net.getEdge(edge_id).getLanes())
            position = random.uniform(0, lane.getLength())
            charger_id = f"charger_dyn_{self.charger_counter}"
            self.charger_counter += 1
            self.active_chargers.append((charger_id, lane.getID(), position))
            print(f"Dynamically added charger {charger_id} on lane {lane.getID()} at position {position}")

    def _spawn_taxis_at_runtime(self, num_taxis):
        print(f"Adding {num_taxis} taxis dynamically...")
        for _ in range(num_taxis):
            start_edge = random.choice(self.valid_edges)
            try:
                taxi_id = f"taxi_dyn_{self.taxi_counter}"
                self.taxi_counter += 1
                self.taxi_ids.append(taxi_id)
                traci.route.add(f"route_{taxi_id}", [start_edge])
                traci.vehicle.add(
                    taxi_id,
                    routeID=f"route_{taxi_id}",
                    typeID="taxi",
                    departPos="random",
                    departLane="best",
                    departSpeed="max",
                )
                print(f"Dynamically added taxi {taxi_id}")
            except traci.exceptions.TraCIException as e:
                print(f"Error adding taxi dynamically: {e}")

    def _remove_people(self, num_people):
        """Removes people from the simulation."""
        print(f"Attempting to remove {num_people} people dynamically...")
        for _ in range(num_people):
            try:
                # Fetch active and valid person IDs
                active_person_ids = [pid for pid in traci.person.getIDList() if pid in self.person_ids]

                # Validate removable people: not in a taxi or reserved
                removable_person_ids = [
                    pid for pid in active_person_ids
                    if not traci.person.getTaxiReservations(pid) and not traci.person.getVehicle(pid)
                ]

                if removable_person_ids:
                    person_id = removable_person_ids.pop(0)
                    try:
                        traci.person.remove(person_id)  # Remove from SUMO
                        self.person_ids.remove(person_id)  # Remove from local tracking
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

        print(f"Finished attempting to remove {num_people} people.")





    def _remove_taxis(self, num_taxis):
        print(f"Removing {num_taxis} taxis dynamically...")
        for _ in range(num_taxis):
            active_taxi_ids = [tid for tid in traci.vehicle.getIDList() if tid in self.taxi_ids]
            if active_taxi_ids:
                taxi_id = active_taxi_ids.pop(0)
                try:
                    traci.vehicle.remove(taxi_id)
                    self.taxi_ids.remove(taxi_id)
                    print(f"Removed taxi {taxi_id}")
                except traci.exceptions.TraCIException as e:
                    print(f"Error removing taxi {taxi_id}: {e}")
            else:
                print("No more taxis to remove.")
                break

    def _remove_chargers(self, num_chargers):
        print(f"Removing {num_chargers} chargers dynamically...")
        for _ in range(num_chargers):
            if self.active_chargers:
                charger_id, lane_id, position = self.active_chargers.pop(0)
                print(f"Removed charger {charger_id} from lane {lane_id} at position {position}.")
            else:
                print("No more chargers to remove.")
                break

