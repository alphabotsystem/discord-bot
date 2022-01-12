from helpers import constants


class Presets(object):
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
