from os import environ
from asyncio import CancelledError
from traceback import format_exc

import discord
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.ext import commands

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import Processor


class ConvertCommand(commands.Cog):
	def __init__(self, bot, create_request, database, logging):
		self.bot = bot
		self.create_request = create_request
		self.database = database
		self.logging = logging

	@slash_command(name="convert", description="Convert between currencies, rates and assets.")
	async def convert(
		self,
		ctx,
		fromTicker: Option(str, "Ticker to convert from.", name="from"),
		toTicker: Option(str, "Ticker to convert to.", name="to"),
		amount: Option(float, "Amount to convert.", name="amount")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			payload, quoteText = await Processor.process_conversion(request, fromTicker.upper(), toTicker.upper(), amount)

			if payload is None:
				errorMessage = "Requested conversion is not available." if quoteText is None else quoteText
				embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Conversion not available", icon_url=static_storage.icon_bw)
				quoteMessage = await ctx.interaction.edit_original_message(embed=embed)
				try: await quoteMessage.add_reaction("☑")
				except: pass
			else:
				embed = discord.Embed(title="{} ≈ {}".format(payload["quotePrice"], payload["quoteConvertedPrice"]), color=constants.colors[payload["messageColor"]])
				embed.set_author(name="Conversion", icon_url=static_storage.icon)
				await ctx.interaction.edit_original_message(embed=embed)

			await self.database.document("discord/statistics").set({request.snapshot: {"convert": Increment(1)}}, merge=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /convert {fromTicker} {toTicker} {amount}")
