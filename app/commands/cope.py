from os import environ
from asyncio import CancelledError
from traceback import format_exc

from discord import Embed, ButtonStyle, Interaction
from discord.commands import slash_command, Option
from discord.ui import View, button, Button

from google.cloud.firestore import Increment

from helpers import constants
from assets import static_storage
from Processor import Processor

from commands.base import BaseCommand, Confirm
from commands.ichibot import Ichibot


COPE_CONSENSUS_VOTE_TESTING = [414498292655980583, 824445607585775646]
if not environ["PRODUCTION_MODE"]: COPE_CONSENSUS_VOTE_TESTING = [926518026457739304]


class CopeVoteCommand(BaseCommand):
	@slash_command(name="vote", description="Start Cope consensus voting poll.", guild_ids=COPE_CONSENSUS_VOTE_TESTING)
	async def vote(
		self,
		ctx,
		tickerId: Option(str, "Ticker id of an asset.", name="ticker"),
		votePeriod: Option(float, "Consensus vote duration in minutes.", name="duration"),
		longCommand: Option(str, "Ichibot command to execute if consensus to go long is reached.", name="long"),
		shortCommand: Option(str, "Ichibot command to execute if consensus to go short is reached.", name="short"),
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			if all([str(role.id) not in request.guildProperties["settings"].get("cope", {}).get("holding", []) for role in ctx.author.roles]):
				embed = Embed(title=":dart: You don't have the permission to hold a vote.", color=constants.colors["gray"])
				embed.set_author(name="Alpha", icon_url=static_storage.icon_bw)
				await ctx.interaction.edit_original_message(embed=embed)

			else:
				if votePeriod > 60 or votePeriod < 1:
					ctx.interaction.edit_original_message(embed=Embed(title="Vote can only be held anywhere from a minute up to an hour.", color=constants.colors["gray"]))
					return

				outputMessage, task = await Processor.process_quote_arguments(request, ["ftx"], tickerId=tickerId.upper(), platformQueue=["Ichibot"])

				if outputMessage is not None:
					embed = Embed(title=outputMessage, description="If the issue persists, please contact support.", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					await ctx.interaction.edit_original_message(embed=embed)
					return

				currentTask = task.get(task.get("currentPlatform"))
				ticker = currentTask.get("ticker")
				copePoolAccountId = "TPeMv6ZJvRZ1QLw0ivCRkCzoirU2" if environ["PRODUCTION_MODE"] else "ebOX1w1N2DgMtXVN978fnL0FKCP2"

				if ticker.get("exchange").get("id") != "ftx":
					embed = Embed(title="Cope consensus trading is only available on FTX.", color=constants.colors["gray"])
					await ctx.interaction.edit_original_message(embed=embed)
					return

				origin = "{}_{}_ichibot".format(copePoolAccountId, request.authorId)

				if origin in Ichibot.sockets:
					socket = Ichibot.sockets.get(origin)
				else:
					socket = Processor.get_direct_ichibot_socket(origin)
					Ichibot.sockets[origin] = socket
					bot.loop.create_task(Ichibot.process_ichibot_messages(origin, message.author))

				await socket.send_multipart([copePoolAccountId.encode(), b"ftx", b"init"])

				embed = Embed(title="Ichibot connection to FTX is being initiated.", color=constants.colors["deep purple"])
				embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
				await ctx.author.send(embed=embed)

				voteMajority = request.guildProperties["settings"]["cope"].get("majority", 70)
				voteMinimum = request.guildProperties["settings"]["cope"].get("minimum", 20)
				allowedVoters = request.guildProperties["settings"]["cope"].get("voting", [])
				logChannelId = request.guildProperties["settings"]["channels"].get("private")
				logChannel = None if logChannelId is None else bot.get_channel(int(logChannelId))

				confirmation = Confirm(userId=request.authorId)
				confirmationText = "Participants will be voting for {} minutes on a directional bet on {}. A consensus will be reached if {:,.1f} % of votes agree and at least {} votes are cast.".format(votePeriod, ticker.get("id"), voteMajority, voteMinimum)
				embed = Embed(title="Confirm the consensus poll.", description=confirmationText, color=constants.colors["pink"])
				embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
				await ctx.interaction.edit_original_message(embed=embed, view=confirmation)
				await confirmation.wait()

				if confirmation.value is None or not confirmation.value:
					embed = Embed(title="Poll has been canceled.", description="~~{}~~".format(confirmationText), color=constants.colors["gray"])
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					await ctx.interaction.edit_original_message(embed=embed, view=None)

				else:
					await socket.send_multipart([copePoolAccountId.encode(), b"", "instrument {}".format(ticker.get("symbol")).encode()])

					poll = VotingActions(log=logChannel)
					embed = Embed(title="Vote on the next trade for {} ({})".format(ticker.get("id"), ticker.get("exchange").get("name")), description="No votes have been received yet.", color=constants.colors["light blue"])
					embed.add_field(name="Vote concludes in {:,.1f} minutes. Reaction is removed when your vote is received.".format(votePeriod / 60), value="If consensus is reached, `{}` or `{}` will be executed via Ichibot to long or short respectively. ".format(longCommand, shortCommand), inline=False)
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					voteMessage = await ctx.interaction.edit_original_message(embed=embed, view=poll)
					await sleep(votePeriod * 60.0)

					try:
						await ctx.interaction.original_message()
					except:
						return

					# Count votes
					totalLong = float(len(poll.longVoters))
					totalShort = float(len(poll.shortVoters))
					totalSkip = float(len(poll.skipVoters))
					totalVotes = int(totalLong + totalShort + totalSkip)

					if totalVotes == 0:
						embed = Embed(title="No consensus has been reached.", description="There were no participants in the vote. No command has been executed via Ichibot.".format(totalVotes, totalLong / totalVotes * 100, totalShort / totalVotes * 100, totalSkip / totalVotes * 100), color=constants.colors["deep purple"])
						embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
						await ctx.interaction.edit_original_message(embed=embed)
					elif totalVotes >= voteMinimum and totalLong / totalVotes >= voteMajority / 100.0:
						await socket.send_multipart([copePoolAccountId.encode(), b"", longCommand.encode()])
						embed = Embed(title="Consensus has been reached, community voted to go long on {}!".format(ticker.get("id")), description="{:,.1f} % out of {} participants voted to go long. `{}` is being executed via Ichibot.".format(totalLong / totalVotes * 100, totalVotes, longCommand), color=constants.colors["deep purple"])
						embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
						await ctx.interaction.edit_original_message(embed=embed)
					elif totalVotes >= voteMinimum and totalShort / totalVotes >= voteMajority / 100.0:
						await socket.send_multipart([copePoolAccountId.encode(), b"", shortCommand.encode()])
						embed = Embed(title="Consensus has been reached, community voted to go short on {}!".format(ticker.get("id")), description="{:,.1f} % out of {} participants voted to go short. `{}` is being executed via Ichibot.".format(totalShort / totalVotes * 100, totalVotes, shortCommand), color=constants.colors["deep purple"])
						embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
						await ctx.interaction.edit_original_message(embed=embed)
					elif totalVotes >= voteMinimum and totalSkip / totalVotes >= voteMajority / 100.0:
						embed = Embed(title="Consensus has been reached, community voted to skip this trade on {}!".format(ticker.get("id")), description="{:,.1f} % out of {} participants voted to skip. No command has been executed via Ichibot.".format(len(totalSkip) / totalVotes * 100, totalVotes), color=constants.colors["deep purple"])
						embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
						await ctx.interaction.edit_original_message(embed=embed)
					else:
						embed = Embed(title="No consensus has been reached.", description="{} participants voted, {:,.1f} % of which to go long, {:,.1f} % to go short and {:,.1f} % to skip. No command has been executed via Ichibot.".format(totalVotes, totalLong / totalVotes * 100, totalShort / totalVotes * 100, totalSkip / totalVotes * 100), color=constants.colors["deep purple"])
						embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
						await ctx.interaction.edit_original_message(embed=embed)

					try:
						await logChannel.send(content="Voted to long: {}".format(", ".join(poll.longVoters)))
						await logChannel.send(content="Voted to short: {}".format(", ".join(poll.shortVoters)))
						await logChannel.send(content="Votes to skip: {}".format(", ".join(poll.skipVoters)))
					except: pass

				await self.database.document("discord/statistics").set({request.snapshot: {"vote": Increment(1)}}, merge=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user="{}: /vote".format(ctx.author.id))


class VotingActions(View):
	def __init__(self, log=None):
		super().__init__(timeout=None)
		self.allVotes = []
		self.longVoters = []
		self.shortVoters = []
		self.skipVoters = []
		self.logChannel = log

	@button(label="Long", style=ButtonStyle.green)
	async def confirm(self, button: Button, interaction: Interaction):
		if interaction.user not in self.allVotes:
			self.allVotes.append(interaction.user)
			await send_vote_confirmation(user, "long", True)
		elif interaction.user not in self.longVoters:
			await send_vote_confirmation(user, "long", False)

		if interaction.user not in self.longVoters: self.longVoters.append(interaction.user)
		if interaction.user in self.shortVoters: self.shortVoters.remove(interaction.user)
		if interaction.user in self.skipVoters: self.skipVoters.remove(interaction.user)

		embed = Embed(description="Your vote has been saved.", color=constants.colors["gray"])
		embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
		await interaction.response.send_message(embed=embed)

	@button(label="Short", style=ButtonStyle.red)
	async def cancel(self, button: Button, interaction: Interaction):
		if interaction.user not in self.allVotes:
			self.allVotes.append(interaction.user)
			await send_vote_confirmation(user, "short", True)
		elif interaction.user not in self.shortVoters:
			await send_vote_confirmation(user, "short", False)

		if interaction.user in self.longVoters: self.longVoters.remove(interaction.user)
		if interaction.user not in self.shortVoters: self.shortVoters.append(interaction.user)
		if interaction.user in self.skipVoters: self.skipVoters.remove(interaction.user)

		embed = Embed(description="Your vote has been saved.", color=constants.colors["gray"])
		embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
		await interaction.response.send_message(embed=embed)

	@button(label="Skip", style=ButtonStyle.gray)
	async def cancel(self, button: Button, interaction: Interaction):
		if interaction.user not in self.allVotes:
			self.allVotes.append(interaction.user)
			await send_vote_confirmation(user, "skip", True)
		elif interaction.user not in self.skipVoters:
			await send_vote_confirmation(user, "skip", False)

		if interaction.user in self.longVoters: self.longVoters.remove(interaction.user)
		if interaction.user in self.shortVoters: self.shortVoters.remove(interaction.user)
		if interaction.user not in self.skipVoters: self.skipVoters.append(interaction.user)

		embed = Embed(description="Your vote has been saved.", color=constants.colors["gray"])
		embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
		await interaction.response.send_message(embed=embed)

	async def send_vote_confirmation(_user, _side, isChange):
		if isChange:
			if self.logChannel is not None:
				try: await self.logChannel.send(content="{} voted to {}".format(_user.mention, _side))
				except: pass
			votesSummaryText = ", ".join(allVotes[-20:])
			if len(allVotes) == 21:
				votesSummaryText = ", ".join(allVotes[-21:-1]) + " and " + allVotes[-1]
			elif len(allVotes) > 21:
				votesSummaryText += " and {} others".format(len(allVotes) - 20)
			votesSummaryText += " voted so far."
			embed.description = votesSummaryText
			try: await voteMessage.edit(embed=embed)
			except: pass
		else:
			if self.logChannel is not None:
				try: await self.logChannel.send(content="{} changed their vote to {}".format(_user.mention, _side))
				except: pass