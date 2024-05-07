from dotenv import load_dotenv
import os

from flask import Flask, render_template, request, jsonify
import folium
import googlemaps
import math
import random
import overpy
import requests

load_dotenv()

# Initialise GoogleMaps client
maps_api_key = os.getenv("GMAPS_API_KEY")
gmaps = googlemaps.Client(key=maps_api_key)

# Set up Overpass API
op_api = overpy.Overpass()

# Mapbox
mb_api_key = os.getenv("MAPBOX_API_KEY")

# Create app instance of Flask
app = Flask(__name__)


@app.route('/', methods=['GET', 'POST'])
def index():
    # Create a map centered on Stockholm
    m = folium.Map(location=[59.3293, 18.0686], zoom_start=12)

    # Render the HTML template with the map
    return render_template('index.html', map=m._repr_html_())


@app.route('/course', methods=['GET', 'POST'])
def generate_course_handler():
    # Get user input from the AJAX request
    user_input = request.json

    # Extract start_location and distance from user_input
    start_location = user_input.get('location')
    distance = float(user_input.get('distance'))

    # start_location = request.form['location']
    # distance = float(request.form['distance'])

    # Call generate_course_map to return info required for updated map
    course_data = generate_course_map(start_location, distance)

    # Return course_data as part of JSON response for AJAX request
    return jsonify({"course_data": course_data})

def generate_course_map(start_location, distance):
    # Geocode the location
    start_geocode = gmaps.geocode(start_location)[0]['geometry']['location']

    # Get waypoints and centre
    way_points, centre = create_waypoints(start_geocode, distance)

    # Get route data for route plotted between waypoints
    route_data = plot_route(way_points)

    # Get coords for PolyLine - extract geometry and reverse so coords are lat, lon
    route_coords = [c[::-1] for c in route_data['geometry']['coordinates']]

    # Get total route distance
    route_distance_km = round(route_data['distance'] / 1000, 2)

    # Get turn-by-turn directions
    directions = get_directions(route_data)

    # Create a map centered at the route centre
    route_map = folium.Map(location=[centre[0], centre[1]], zoom_start=12)

    # Add markers for the start and end locations
    folium.Marker([start_geocode['lat'],
                   start_geocode['lng']],
                  tooltip='Starting Location',
                  icon=folium.Icon(color="green", icon="flag-checkered", prefix="fa")).add_to(route_map)

    # Add markers for waypoints (excluding start/end)
    for waypoint in way_points[1:-1]:
        folium.Marker([waypoint[0], waypoint[1]]).add_to(route_map)

    # Add route to map
    folium.PolyLine(locations=route_coords, color='blue', weight=2, opacity=1).add_to(route_map)

    # Zoom map to fit the route
    a, b = zip(*route_coords)
    sw = (min(a), min(b))
    ne = (max(a), max(b))
    route_map.fit_bounds([sw, ne])

    # Create dictionary of info to pass back via AJAX request
    course_data = {"map": route_map._repr_html_(),
                   "location": start_location,
                   "distance": distance,
                   "route_distance": route_distance_km,
                   "directions": directions
                   }

    return course_data


def create_waypoints(origin, distance):
    """Generates a list of way_points around a circle starting from the start location, in a random direction."""
    # Approximation for degrees to kilometres (based on Stockholm)
    lat_deg_to_km = 1 / 111
    lng_deg_to_km = 1 / 55

    # User input
    start_coords = (origin['lat'], origin['lng'])
    distance_km = distance

    # Radius
    scaling_factor = 0.7
    radius = (distance_km / (2 * math.pi)) * scaling_factor

    # Random direction
    direction = 2 * math.pi * random.random()

    # Centre of circle
    centre_x = start_coords[0] + radius * math.cos(direction) * lat_deg_to_km
    centre_y = start_coords[1] + radius * math.sin(direction) * lng_deg_to_km

    # Number and angle of way_points to plot around circle
    n = 6
    a = n // 2
    angles = [math.pi * x / a for x in range(-a, a + 1)]
    wp_angles = [direction + angle for angle in angles]

    points = [(centre_x + radius * math.cos(i) * lat_deg_to_km,
               centre_y + radius * math.sin(i) * lng_deg_to_km) for i in wp_angles]

    # Find coords of nearest road for each point
    way_points = coords_to_waypoint(points)

    # Create centre coords tuple
    centre = (centre_x, centre_y)

    return way_points, centre


def coords_to_waypoint(coords):
    """Takes a list of coordinate tuples as input.
    For each point excluding the starting point, attempts to find a
    highway nearby, and if successful selects first node coordinates from highway.
    Returns list of nearby highway node coordinate tuples."""
    way_points = []
    for idx, point in enumerate(coords):
        # Don't adjust starting coordinates
        if idx == 0:
            way_points.append(point)
        # Fetch first node of first way in vicinty of x metres. Could be scaled to route distance?
        x = 50
        # If no way point, expand search diameter by 3 factors of 5. Ignore if still no result.
        for attempt in range(3):
            try:
                result = op_api.query(f"""way(around:{x}, {point[0]}, {point[1]})["highway"];(._;>;);out body;""")
                first_way_node = result.ways[0].nodes[0]
            except:
                x *= 5
            else:
                way_points.append((float(first_way_node.lat), float(first_way_node.lon)))
                # print(f'{point} => {(float(first_way_node.lat), float(first_way_node.lon))} within {x} metres.')
                break

    return way_points


def plot_route(way_points: list):
    """Requests walking route between way_points from Mapbox, returns all route coords for PolyLine plotting."""
    # Create string of waypoints (lon,lat;) for Mapbox url request
    wp_str = ""
    for idx, point in enumerate(way_points):
        if idx == len(way_points) - 1:
            wp_str += f"{point[1]},{point[0]}"
        else:
            wp_str += f"{point[1]},{point[0]};"

    # Construct the Mapbox request URL
    mapbox_url = f"https://api.mapbox.com/directions/v5/mapbox/walking/{wp_str}"
    payload = {"geometries": "geojson",
               "access_token": {mb_api_key},
               "exclude": "ferry",
               "continue_straight": "true",
               "steps": "true"}

    # &continue_straight=true

    # Send the request to Mapbox API
    response = requests.get(mapbox_url, payload)
    data = response.json()

    # Extract route data
    route_data = data['routes'][0]

    return route_data


def get_directions(route_data: dict):
    """Extracts maneuver instructions from each step in each leg and returns them in a list."""
    directions = []
    for idx, leg in enumerate(route_data['legs']):
        for step in route_data['legs'][idx]['steps'][:-1]:
            directions.append(f"{step['maneuver']['instruction']} ({round(step['distance'], 0)} meters)")

    return directions


if __name__ == '__main__':
    app.run(debug=True)
