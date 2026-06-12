from os import environ
from time import time
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed
from discord.commands import slash_command, Option
from discord.errors import NotFound

from google.cloud.firestore import Increment

from helpers.utils import get_incorrect_usage_description
from helpers import constants
from assets import static_storage

from commands.base import BaseCommand


class DetailsCommand(BaseCommand):
	@slash_command(name="info", description="Pull up asset information of stocks and cryptocurrencies.")
	async def info(
		self,
		ctx,
		query: Option(str, "Ticker id of an asset.", name="query")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			await ctx.defer()
			response = await self.render_via_v2("info " + query, request)

			if not response.get("ok"):
				message = response.get("error") or "Requested asset information is not available."
				embed = Embed(title=message, description=get_incorrect_usage_description(self.bot.user.id, "https://www.alpha.bot/features/asset-details"), color=constants.colors["gray"])
				embed.set_author(name="Data not available", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return

			# v2's `info` verb posts one markdown block per message (overview card,
			# then statement tables), each already under Discord's 2000-char limit.
			messages = [text for post in response.get("posts", []) if (text := (post.get("text") or "").strip())]

			if len(messages) == 0:
				embed = Embed(title=f"Requested asset information for `{query}` is not available.", color=constants.colors["gray"])
				embed.set_author(name="Data not available", icon_url=static_storage.error_icon)
				try: await ctx.interaction.edit_original_response(embed=embed)
				except NotFound: pass
				return

			try: await ctx.interaction.edit_original_response(content=messages[0])
			except NotFound: pass
			for message in messages[1:]:
				try: await ctx.followup.send(content=message)
				except NotFound: pass

			await self.database.document("discord/statistics").set({request.snapshot: {"info": Increment(1)}}, merge=True)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /info {query}")
			await self.unknown_error(ctx)