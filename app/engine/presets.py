from helpers import constants


class Presets(object):
	@staticmethod
	def update_presets(settings, add="", shortcut="", remove="", messageRequest=None):
		add = add.replace(",", "")

		if add != "" and shortcut != "":
			if "commandPresets" not in settings: settings["commandPresets"] = []
			if len(settings["commandPresets"]) >= 25:
				return settings, ("Too many presets", "You can only have up to {} presets.".format(25), "gray")

			if add in ["preset"]:
				return settings, ("Invalid preset name", "Preset name cannot be `{}`.".format(add), "gray")
			if add in constants.commandKeywords:
				return settings, ("Invalid preset name", "A command prefix cannot be used as a preset name.", "gray")
			if len(add) < 5:
				return settings, ("Invalid preset name", "Preset name must be at least 5 characters long to prevent accidental triggers.", "gray")

			if shortcut.startswith("alpha "):
				return settings, ("Invalid preset shortcut", "Assistant functionality cannot be used in a preset.", "gray")

			if not shortcut.startswith(tuple(constants.commandWakephrases)):
				return settings, ("Invalid preset shortcut", "Preset doesn't initiate a command.", "gray")

			for phrase in settings["commandPresets"]:
				if phrase["phrase"] == add:
					return settings, ("Preset already exists", "Preset named `{}` already exists. You can use `preset list` and remove the existing preset.".format(add), "gray")
			settings["commandPresets"].append({"phrase": add, "shortcut": shortcut})
			return settings, ("Preset added", "Preset was successfully added.", "deep purple")
		elif remove != "":
			for phrase in settings["commandPresets"]:
				if phrase["phrase"] == remove:
					settings["commandPresets"].remove(phrase)
					return settings, ("Preset removed", "Preset was successfully removed.", "deep purple")
			return settings, ("Preset not found", "Preset named `{}` was not found".format(remove), "gray")
		return settings, ("Something went wrong", "Looks like something went wrong.", "gray")

	@staticmethod
	def process_presets(r, settings):
		if "commandPresets" in settings:
			usedPresets = []
			rawParts = r.split(" ")
			for i in range(len(rawParts)):
				initial = rawParts[i]
				for preset in settings["commandPresets"]:
					if preset["phrase"] == initial.replace(",", ""):
						rawParts[i] = preset["shortcut"] + "," if initial.endswith(",") else preset["shortcut"]
						usedPresets.append(preset)
				if initial == rawParts[i]:
					return r, False, []
			raw = " ".join(rawParts)
			return raw, len(usedPresets) != 0, usedPresets
		else:
			return r, False, []
