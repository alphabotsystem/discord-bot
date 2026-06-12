from os import environ
from time import time
from random import randint
from asyncio import gather, CancelledError
from traceback import format_exc

from discord import Embed, ButtonStyle, Interaction, File
from discord.commands import SlashCommandGroup, Option
from discord.ui import View, button, Button
from discord.errors import NotFound
from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage

from commands.base import BaseCommand, ActionsView, autocomplete_fgi_type, files_from_posts


class LookupCommand(BaseCommand):
	lookupGroup = SlashCommandGroup("lookup", "Look up or screen the market for various properties.")

	@lookupGroup.command(name="fgi", description="Look up the current and historic fear & greed index.")
	async def fgi(
		self,
		ctx,
		assetType: Option(str, "Fear & greed market type", name="market", autocomplete=autocomplete_fgi_type, required=False, default=""),
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			# v2's fgi grammar requires a market; default to crypto when unspecified.
			market = "crypto"
			if assetType != "":
				if assetType.lower() in ("crypto", "stocks"):
					market = assetType.lower()
				else:
					embed = Embed(title="Asset type is invalid. Only stocks and crypto markets are supported.", color=constants.colors["gray"])
					embed.set_author(name="Invalid market", icon_url=static_storage.error_icon)
					try: await ctx.respond(embed=embed)
					except NotFound: pass
					return

			await ctx.defer()
			response = await self.render_via_v2("fgi " + market, request)

			files, embeds = [], []
			if not response.get("ok"):
				message = response.get("error") or "Requested chart is not available."
				embed = Embed(title=message, color=constants.colors["gray"])
				embed.set_author(name="Chart not available", icon_url=static_storage.error_icon)
				embeds.append(embed)
			else:
				files = files_from_posts(response.get("posts", []), request.authorId)

			actions = ActionsView(user=ctx.author, command=ctx.command.mention)
			try: await ctx.interaction.edit_original_response(embeds=embeds, files=files, view=actions)
			except NotFound: pass

			await self.database.document("discord/statistics").set({request.snapshot: {"c": Increment(1)}}, merge=True)
			await self.log_request_v2("charts", request, response.get("meta", {}))
			await self.cleanup(ctx, request, removeView=True)

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /lookup fgi {assetType}")
			await self.unknown_error(ctx)