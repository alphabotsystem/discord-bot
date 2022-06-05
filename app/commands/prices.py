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
from Processor import Processor

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
			payload, quoteText = await Processor.process_task("quote", request.authorId, task)

			if payload is None or "quotePrice" not in payload:
				errorMessage = f"Requested price for `{currentTask.get('ticker').get('name')}` is not available." if quoteText is None else quoteText
				embed = Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
			else:
				currentTask = task.get(payload.get("platform"))
				if payload.get("platform") in ["Alternative.me"]:
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
			s = perf_counter()
			request = await self.create_request(ctx)
			if request is None: return

			print(perf_counter() - s)

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

				s = perf_counter()
				outputMessage, task = await Processor.process_quote_arguments(request, partArguments[1:], defaultPlatforms, tickerId=partArguments[0].upper())

				print(perf_counter() - s)

				if outputMessage is not None:
					embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/prices).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
					return
				
				tasks.append(task)

			await self.respond(ctx, request, tasks)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /p {' '.join(arguments)}")
			await self.unknown_error(ctx)

	@slash_command(name="price", description="Fetch stock, crypto and forex quotes.")
	async def price(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker"),
		assetType: Option(str, "Asset class of the ticker.", name="type", autocomplete=BaseCommand.get_types, required=False, default=""),
		venue: Option(str, "Venue to pull the price from.", name="venue", autocomplete=BaseCommand.get_venues, required=False, default="")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			defaultPlatforms = request.get_platform_order_for("p", assetType=assetType)
			preferredPlatforms = BaseCommand.sources["p"].get(assetType)
			platforms = [e for e in defaultPlatforms if preferredPlatforms is None or e in preferredPlatforms]

			arguments = [venue]
			outputMessage, task = await Processor.process_quote_arguments(request, arguments, platforms, tickerId=tickerId.upper())

			if outputMessage is not None:
				embed = Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/prices).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)
				return

			await self.respond(ctx, request, [task])

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id}: /price {tickerId} type:{assetType} venue:{venue}")
			await self.unknown_error(ctx)