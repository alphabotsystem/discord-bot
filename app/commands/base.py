from discord.ext.commands import Cog
from discord import AutocompleteContext

from Processor import Processor


class BaseCommand(Cog):
	def __init__(self, bot, create_request, database, logging):
		self.bot = bot
		self.create_request = create_request
		self.database = database
		self.logging = logging

	async def get_exchanges(cls, ctx: AutocompleteContext):
		return []