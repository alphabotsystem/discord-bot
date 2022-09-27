from os import environ
from time import perf_counter
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed
from discord.embeds import EmptyEmbed
from discord.commands import slash_command, Option
from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import process_quote_arguments, process_task

from commands.base import BaseCommand


class PriceCommand(BaseCommand):
	async def respond(
		self,
		ctx,
		request,
		tasks
	):
		embeds = []
		for task in tasks:
			currentTask = task.get(task.get("currentPlatform"))
			payload, responseMessage = await process_task(task, "quote")

			if payload is None or "quotePrice" not in payload:
				errorMessage = f"Requested price for `{currentTask.get('ticker').get('name')}` is not available." if responseMessage is None else responseMessage
				embed = Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
			else:
				currentTask = task.get(payload.get("platform"))
				if payload.get("platform") in ["Alternative.me", "CNN Business"]:
					embed = Embed(title=f"{payload['quotePrice']} *({payload['change']})*", description=payload.get("quoteConvertedPrice", EmptyEmbed), color=constants.colors[payload["messageColor"]])
					embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
					embed.set_footer(text=payload["sourceText"])
				else:
					embed = Embed(title="{}{}".format(payload["quotePrice"], f" *({payload['change']})*" if "change" in payload else ""), description=payload.get("quoteConvertedPrice", EmptyEmbed), color=constants.colors[payload["messageColor"]])
					embed.set_author(name=payload["title"], icon_url=payload.get("thumbnailUrl"))
					embed.set_footer(text=payload["sourceText"])

			embeds.append(embed)
		
		await ctx.interaction.edit_original_message(embeds=embeds)
		await self.database.document("discord/statistics").set({request.snapshot: {"p": Increment(len(tasks))}}, merge=True)

	@slash_command(name="p", description="Fetch stock and crypto prices, forex rates, and other instrument data. Command for power users.")
	async def p(
		self,
		ctx,
		arguments: Option(str, "Request arguments starting with ticker id.", name="arguments")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			defaultPlatforms = request.get_platform_order_for("p")

			parts = arguments.split(",")
			tasks = []

			if len(parts) > 5:
				embed = Embed(title="Only up to 5 requests are allowed per command.", color=constants.colors["gray"])
				embed.set_author(name="Too many requests", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			for part in parts:
				partArguments = part.lower().split()
				if len(partArguments) == 0: continue

				responseMessage, task = await process_quote_arguments(partArguments[1:], defaultPlatforms, tickerId=partArguments[0].upper())

				if responseMessage is not None:
					embed = Embed(title=responseMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/features/prices).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
					return
				
				tasks.append(task)

			await self.respond(ctx, request, tasks)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /p {arguments}")
			await self.unknown_error(ctx)

	@slash_command(name="price", description="Fetch stock, crypto and forex quotes.")
	async def price(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker", autocomplete=BaseCommand.autocomplete_ticker),
		venue: Option(str, "Venue to pull the price from.", name="venue", autocomplete=BaseCommand.autocomplete_venues, required=False, default="")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			platforms = request.get_platform_order_for("p")

			arguments = [venue]
			responseMessage, task = await process_quote_arguments(arguments, platforms, tickerId=tickerId.upper())

			if responseMessage is not None:
				embed = Embed(title=responseMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/features/prices).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.respond(ctx, request, [task])

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /price {tickerId} venue:{venue}")
			await self.unknown_error(ctx)