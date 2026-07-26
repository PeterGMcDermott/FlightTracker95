import math
import time
import requests
from flask import Flask, render_template, jsonify, request
app = Flask(__name__)
# Default Home Base: Franklin, TN
DEFAULT_HOME_LAT = 35.9259
DEFAULT_HOME_LON = -86.8689
DEFAULT_RADIUS_MILES = 30.0
# Airport reference locations
KBNA_LAT, KBNA_LON = 36.1245, -86.6782 # Nashville Intl
AIRLINE_MAP = {
    'SWA': 'Southwest Airlines',
    'DAL': 'Delta Air Lines',
    'AAL': 'American Airlines',
    'UAL': 'United Airlines',
    'ENY': 'Envoy Air (American Eagle)',
    'EDV': 'Endeavor Air (Delta Connection)',
    'RPA': 'Republic Airways',
    'SKW': 'SkyWest Airlines',
    'FDX': 'FedEx Express Cargo',
    'UPS': 'UPS Airlines Cargo',
    'CXK': 'Executive Jet Management',
    'JBU': 'JetBlue Airways',
    'FFT': 'Frontier Airlines',
    'NKS': 'Spirit Airlines',
    'ASA': 'Alaska Airlines',
    'GTI': 'Atlas Air',
    'AWI': 'Air Wisconsin',
    'G7': 'GoJet Airlines',
    'EJA': 'NetJets Aviation'
}
AIRCRAFT_TYPE_MAP = {
    'B737': 'Boeing 737-700/800',
    'B738': 'Boeing 737-800',
    'B739': 'Boeing 737-900',
    'B38M': 'Boeing 737 MAX 8',
    'A320': 'Airbus A320',
    'A321': 'Airbus A321',
    'A319': 'Airbus A319',
    'A20N': 'Airbus A320neo',
    'B752': 'Boeing 757-200',
    'B763': 'Boeing 767-300',
    'B772': 'Boeing 777-200',
    'B789': 'Boeing 787-9 Dreamliner',
    'E75L': 'Embraer E175',
    'E175': 'Embraer E175',
    'CRJ2': 'Bombardier CRJ-200',
    'CRJ7': 'Bombardier CRJ-700',
    'CRJ9': 'Bombardier CRJ-900',
    'C172': 'Cessna 172 Skyhawk',
    'SR22': 'Cirrus SR22',
    'BE20': 'Beechcraft Super King Air',
    'PC12': 'Pilatus PC-12'
}
# Cache for ADSB.lol aircraft metadata (ICAO24 -> {registration, type_code})
