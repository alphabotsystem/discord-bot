from os import environ
from asyncio import CancelledError, sleep
from traceback import format_exc

from zmq import NOBLOCK

from discord import Embed
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.channel import DMChannel
from discord.errors import Forbidden

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import Processor

from commands.base import BaseCommand


SUPPORTED_EXCHANGES = ["ftx", "binance", "binancefutures"]


class Ichibot(object):
	sockets = {}

	async def process_ichibot_messages(origin, author):
		try:
			socket = Ichibot.sockets.get(origin)

			while origin in Ichibot.sockets:
				try:
					messageContent = "```ruby"

					while True:
						try: [messenger, message] = await socket.recv_multipart(flags=NOBLOCK)
						except: break
						print(messenger, message)
						if messenger.decode() == "alpha":
							embed = Embed(title=message.decode(), color=constants.colors["gray"])
							embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
							try: await author.send(embed=embed)
							except: pass
						else:
							message = message.decode()
							if len(message) + len(messageContent) + 4 >= 2000:
								messageContent = messageContent[:1997] + "```"
								try: await author.send(content=messageContent)
								except Forbidden: pass
								messageContent = "```ruby"
							messageContent += "\n" + message

					if messageContent != "```ruby":
						messageContent = messageContent[:1997] + "```"
						try: await author.send(content=messageContent)
						except Forbidden: pass
					await sleep(1)

				except:
					print(format_exc())
					if environ["PRODUCTION_MODE"]: logging.report_exception(user=origin)

			socket.close()

		except:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: logging.report_exception(user=origin)


class IchibotCommand(BaseCommand):
	ichibotGroup = SlashCommandGroup("ichibot", "Use Ichibot crypto trading terminal right in Discord.")

	@ichibotGroup.command(name="login", description="Login into Ichibot crypto trading terminal to open a trading session in Discord.")
	async def convert(
		self,
		ctx,
		exchange: Option(str, "Crypto exchange to connect to.", name="exchange", autocomplete=BaseCommand.get_venues),
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			exchangeId = exchange.lower().replace(" ", "")

			if request.is_registered():
				if exchangeId not in SUPPORTED_EXCHANGES:
					embed = Embed(title="`{}` is not a valid argument".format(exchange), description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/ichibot).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.ichibot)
					await ctx.interaction.edit_original_message(embed=embed)
					return

				origin = "{}_{}_ichibot".format(request.accountId, request.authorId)

				if origin in Ichibot.sockets:
					socket = Ichibot.sockets.get(origin)
				else:
					socket = Processor.get_direct_ichibot_socket(origin)
					Ichibot.sockets[origin] = socket
					self.bot.loop.create_task(Ichibot.process_ichibot_messages(origin, ctx.author))

				await socket.send_multipart([request.accountId.encode(), exchangeId.encode(), b"init"])

				try:
					embed = Embed(title="Ichibot connection is being initiated.", color=constants.colors["deep purple"])
					embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
					await ctx.interaction.edit_original_message(embed=embed)

					if not isinstance(ctx.channel, DMChannel):
						await ctx.author.send(embed=embed)


				except Forbidden:
					embed = Embed(title="Ichibot connection is being initiated, however the bot cannot DM you.", description="A common reason for this is that the bot is blocked, or that your DMs are disabled. Before you can start trading you must enable open your Direct Messages with Alpha Bot.", color=constants.colors["deep purple"])
					embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
					await ctx.interaction.edit_original_message(embed=embed)

			else:
				embed = Embed(title=":dart: You must have an Alpha Account connected to your Discord to execute live trades.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), connect your account with your Discord profile, and add an API key.", color=constants.colors["deep purple"])
				embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
				await ctx.interaction.edit_original_message(embed=embed)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /convert {} {} {}".format(ctx.author.id, fromTicker, toTicker, amount))
