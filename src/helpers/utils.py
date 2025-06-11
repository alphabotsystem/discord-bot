from datetime import datetime

def add_decimal_zeros(number, digits=8):
	wholePart = str(int(number))
	return digits if wholePart == "0" else max(digits - len(wholePart), 0)

def timestamp_to_date(timestamp):
	return datetime.utcfromtimestamp(timestamp).strftime("%m. %d. %Y, %H:%M")

def get_incorrect_usage_description(origin, link=None):
	if origin == 1229893549986811986:
		return "Find help in the pinned post in this channel."
	elif origin == 1381387199450316920:
		return "For help please refer to our team."
	else:
		return f"Detailed guide with examples is available on [our website]({link})."