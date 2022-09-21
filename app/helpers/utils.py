from time import time
from datetime import datetime

def add_decimal_zeros(number, digits=8):
	wholePart = str(int(number))
	return digits if wholePart == "0" else max(digits - len(wholePart), 0)

def timestamp_to_date(timestamp):
	return datetime.utcfromtimestamp(timestamp).strftime("%m. %d. %Y, %H:%M")