import xml.etree.ElementTree as ET
import json
from pyproj import Transformer

# Parse the .net.xml file
tree = ET.parse('downtown_houston.net.xml')
root = tree.getroot()

# Create a transformer from UTM Zone 15N to WGS84
transformer = Transformer.from_crs("EPSG:32615", "EPSG:4326", always_xy=True)

# Dictionaries to store nodes and features
nodes = {}
features = []

# Extract nodes (junctions)
for node in root.findall('junction'):
    node_id = node.get('id')
    x = float(node.get('x'))
    y = float(node.get('y'))
    # Transform coordinates from UTM to WGS84
    lon, lat = transformer.transform(x, y)
    nodes[node_id] = (lon, lat)

# Extract edges
for edge in root.findall('edge'):
    if edge.get('function') == 'internal':
        continue  # Skip internal edges
    edge_id = edge.get('id')

    # Handle edges with multiple lanes
    lanes = edge.findall('lane')
    for lane in lanes:
        shape = lane.get('shape')
        if not shape:
            continue
        # Parse the shape into coordinate pairs
        coords = []
        for point in shape.strip().split(' '):
            x_str, y_str = point.split(',')
            x = float(x_str)
            y = float(y_str)
            lon, lat = transformer.transform(x, y)
            coords.append([lon, lat])

        # Create a LineString feature
        feature = {
            "type": "Feature",
            "properties": {
                "id": edge_id,
                "lane_id": lane.get('id'),
                "speed": lane.get('speed'),
                "allow": lane.get('allow'),
                "disallow": lane.get('disallow'),
            },
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            }
        }
        features.append(feature)

# Create a FeatureCollection
geojson_data = {
    "type": "FeatureCollection",
    "features": features
}

# Save to a GeoJSON file
with open('network.geojson', 'w') as f:
    json.dump(geojson_data, f)

print("GeoJSON file 'network.geojson' created successfully.")