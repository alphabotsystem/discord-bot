from os import environ
from asyncio import CancelledError, sleep
from traceback import format_exc

from zmq import NOBLOCK

from discord import Embed
from discord.commands import slash_command, SlashCommandGroup, Option
from discord.errors import Forbidden, NotFound

from google.cloud.firestore import Increment

from helpers.utils import get_incorrect_usage_description
from helpers import constants
from assets import static_storage
from Processor import get_direct_ichibot_socket

from commands.base import BaseCommand


SUPPORTED_EXCHANGES = {
	"binance": "binance",
	"bin": "binance",
	"bins": "binance",
	"binanceusdⓢ-m": "binancefutures",
	"binf": "binancefutures",
	"fbin": "binancefutures",
}


class Ichibot(object):
	sockets = {}
	logging = None

	async def process_ichibot_messages(origin, author):
		try:
			socket = Ichibot.sockets.get(origin)

			while origin in Ichibot.sockets:
				try:
					messageContent = "```ansi"

					while True:
						try: [messenger, message] = await socket.recv_multipart(flags=NOBLOCK)
						except: break
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
								messageContent = "```ansi"
							messageContent += "\n" + message

					if messageContent != "```ansi":
						messageContent = messageContent[:1997] + "```"
						try: await author.send(content=messageContent)
						except Forbidden: pass
					await sleep(1)

				except:
					print(format_exc())
					if environ["PRODUCTION"]: Ichibot.logging.report_exception(user=origin)

			socket.close()

		except:
			print(format_exc())
			if environ["PRODUCTION"]: Ichibot.logging.report_exception(user=origin)


class IchibotCommand(BaseCommand):
	ichibotGroup = SlashCommandGroup("ichibot", "Use Ichibot crypto trading terminal right in Discord.")

	@ichibotGroup.command(name="login", description="Login into Ichibot crypto trading terminal to open a trading session in Discord.")
	async def login(
		self,
		ctx,
		exchange: Option(str, "Crypto exchange to connect to.", name="exchange", autocomplete=BaseCommand.autocomplete_venues),
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			exchangeId = SUPPORTED_EXCHANGES.get(exchange.lower().replace(" ", ""))

			if request.is_registered():
				if exchangeId is None:
					embed = Embed(title=f"`{exchange[:229]}` is not a valid argument", description=get_incorrect_usage_description(self.bot.user.id, "https://gitlab.com/Ichimikichiki/ichibot-client-app/-/wikis/home"), color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.ichibot)
					try: await ctx.respond(embed=embed)
					except NotFound: pass
					return

				origin = f"{request.accountId}_{request.authorId}_ichibot"

				if origin in Ichibot.sockets:
					socket = Ichibot.sockets.get(origin)
				else:
					socket = get_direct_ichibot_socket(origin)
					Ichibot.sockets[origin] = socket
					self.bot.loop.create_task(Ichibot.process_ichibot_messages(origin, ctx.author))

				await socket.send_multipart([request.accountId.encode(), exchangeId.encode(), b"init"])

				try:
					embed = Embed(title="Ichibot connection is being initiated.", color=constants.colors["deep purple"])
					embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
					try: await ctx.respond(embed=embed)
					except NotFound: pass
				except Forbidden:
					embed = Embed(title="Ichibot connection is being initiated, however the bot cannot DM you.", description=f"A common reason for this is that the bot is blocked, or that your DMs are disabled. Before you can start trading you must enable open your Direct Messages in any server you share with {self.bot.user.name} Bot.", color=constants.colors["deep purple"])
					embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
					try: await ctx.respond(embed=embed)
					except NotFound: pass

			else:
				embed = Embed(title=":dart: You must have an Alpha.bot account connected to your Discord to execute live trades.", description="[Sign up for a free account on our website](https://www.alpha.bot/signup). If you already signed up, [sign in](https://www.alpha.bot/login), connect your account with your Discord profile, and add an API key.", color=constants.colors["deep purple"])
				embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
				try: await ctx.respond(embed=embed)
				except NotFound: pass

		except CancelledError: pass
		except:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /ichibot login {exchange}")
			await self.unknown_error(ctx)