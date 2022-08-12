from os import environ
from asyncio import CancelledError, sleep
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
				if votePeriod > 15 or votePeriod < 1:
					ctx.interaction.edit_original_message(embed=Embed(title="Vote can only be held anywhere from a minute up to 15 minutes.", color=constants.colors["gray"]))
					return

				outputMessage, task = await Processor.process_quote_arguments(request, ["ftx"], ["Ichibot"], tickerId=tickerId.upper())

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

				origin = f"{copePoolAccountId}_{request.authorId}_ichibot"

				if origin in Ichibot.sockets:
					socket = Ichibot.sockets.get(origin)
				else:
					socket = Processor.get_direct_ichibot_socket(origin)
					Ichibot.sockets[origin] = socket
					self.bot.loop.create_task(Ichibot.process_ichibot_messages(origin, ctx.author))

				await socket.send_multipart([copePoolAccountId.encode(), b"ftx", b"init"])

				embed = Embed(title="Ichibot connection to FTX is being initiated.", color=constants.colors["deep purple"])
				embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
				await ctx.author.send(embed=embed)

				voteMajority = request.guildProperties["settings"]["cope"].get("majority", 70)
				voteMinimum = request.guildProperties["settings"]["cope"].get("minimum", 20)
				allowedVoters = request.guildProperties["settings"]["cope"].get("voting", [])
				logChannelId = request.guildProperties["settings"]["channels"].get("private")
				logChannel = None if logChannelId is None else bot.get_channel(int(logChannelId))

				await sleep(2)

				confirmation = Confirm(user=ctx.author)
				confirmationText = "Participants will be voting for {} minutes on a directional bet on {}. A consensus will be reached if {:,.1f} % of votes agree and at least {} votes are cast.".format(votePeriod, ticker.get("id"), voteMajority, voteMinimum)
				embed = Embed(title="Confirm the consensus poll.", description=confirmationText, color=constants.colors["pink"])
				embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
				await ctx.interaction.edit_original_message(embed=embed, view=confirmation)
				await confirmation.wait()

				if confirmation.value is None or not confirmation.value:
					embed = Embed(title="Poll has been canceled.", description=f"~~{confirmationText}~~", color=constants.colors["gray"])
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					await ctx.interaction.edit_original_message(embed=embed, view=None)

				else:
					await socket.send_multipart([copePoolAccountId.encode(), b"", f"instrument {ticker.get('symbol')}".encode()])

					poll = VotingActions(log=logChannel)
					embed = Embed(title=f"Vote on the next trade for {ticker.get('id')} ({ticker.get('exchange').get('name')})", description="No votes have been received yet.", color=constants.colors["light blue"])
					embed.add_field(name="Vote concludes in {:,.1f} minutes.".format(votePeriod), value=f"If consensus is reached, `{longCommand}` or `{shortCommand}` will be executed via Ichibot to long or short respectively. ", inline=False)
					embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
					await ctx.interaction.edit_original_message(embed=embed, view=poll)
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
						embed = Embed(title="No consensus has been reached.", description="There were no participants in the vote. No command has been executed via Ichibot.", color=constants.colors["deep purple"])
						embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
						await ctx.interaction.edit_original_message(embed=embed, view=None)
					elif totalVotes >= voteMinimum and totalLong / totalVotes >= voteMajority / 100.0:
						await socket.send_multipart([copePoolAccountId.encode(), b"", longCommand.encode()])
						embed = Embed(title=f"Consensus has been reached, community voted to go long on {ticker.get('id')}!", description="{:,.1f} % out of {} participants voted to go long. `{}` is being executed via Ichibot.".format(totalLong / totalVotes * 100, totalVotes, longCommand), color=constants.colors["deep purple"])
						embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
						await ctx.interaction.edit_original_message(embed=embed, view=None)
					elif totalVotes >= voteMinimum and totalShort / totalVotes >= voteMajority / 100.0:
						await socket.send_multipart([copePoolAccountId.encode(), b"", shortCommand.encode()])
						embed = Embed(title=f"Consensus has been reached, community voted to go short on {ticker.get('id')}!", description="{:,.1f} % out of {} participants voted to go short. `{}` is being executed via Ichibot.".format(totalShort / totalVotes * 100, totalVotes, shortCommand), color=constants.colors["deep purple"])
						embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
						await ctx.interaction.edit_original_message(embed=embed, view=None)
					elif totalVotes >= voteMinimum and totalSkip / totalVotes >= voteMajority / 100.0:
						embed = Embed(title=f"Consensus has been reached, community voted to skip this trade on {ticker.get('id')}!", description="{:,.1f} % out of {} participants voted to skip. No command has been executed via Ichibot.".format(len(totalSkip) / totalVotes * 100, totalVotes), color=constants.colors["deep purple"])
						embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
						await ctx.interaction.edit_original_message(embed=embed, view=None)
					else:
						embed = Embed(title="No consensus has been reached.", description="{} participants voted, {:,.1f} % of which to go long, {:,.1f} % to go short and {:,.1f} % to skip. No command has been executed via Ichibot.".format(totalVotes, totalLong / totalVotes * 100, totalShort / totalVotes * 100, totalSkip / totalVotes * 100), color=constants.colors["deep purple"])
						embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
						await ctx.interaction.edit_original_message(embed=embed, view=None)

					try:
						await logChannel.send(content=f"Voted to long: {', '.join(poll.longVoters)}")
						await logChannel.send(content=f"Voted to short: {', '.join(poll.shortVoters)}")
						await logChannel.send(content=f"Votes to skip: {', '.join(poll.skipVoters)}")
					except: pass

				await self.database.document("discord/statistics").set({request.snapshot: {"vote": Increment(1)}}, merge=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /vote")
			await self.unknown_error(ctx)


class VotingActions(View):
	def __init__(self, log=None):
		super().__init__(timeout=None)
		self.allVotes = []
		self.longVoters = []
		self.shortVoters = []
		self.skipVoters = []
		self.logChannel = log

	@button(label="Vote long", style=ButtonStyle.green)
	async def longVote(self, button: Button, interaction: Interaction):
		if interaction.user not in self.allVotes:
			self.allVotes.append(interaction.user)
			await self.send_vote_confirmation(interaction, "long", True)
		elif interaction.user not in self.longVoters:
			await self.send_vote_confirmation(interaction, "long", False)

		if interaction.user not in self.longVoters: self.longVoters.append(interaction.user)
		if interaction.user in self.shortVoters: self.shortVoters.remove(interaction.user)
		if interaction.user in self.skipVoters: self.skipVoters.remove(interaction.user)

		embed = Embed(description="Your vote has been saved.", color=constants.colors["gray"])
		embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
		await interaction.response.send_message(embed=embed, ephemeral=True)

	@button(label="Vote short", style=ButtonStyle.red)
	async def shortVote(self, button: Button, interaction: Interaction):
		if interaction.user not in self.allVotes:
			self.allVotes.append(interaction.user)
			await self.send_vote_confirmation(interaction, "short", True)
		elif interaction.user not in self.shortVoters:
			await self.send_vote_confirmation(interaction, "short", False)

		if interaction.user in self.longVoters: self.longVoters.remove(interaction.user)
		if interaction.user not in self.shortVoters: self.shortVoters.append(interaction.user)
		if interaction.user in self.skipVoters: self.skipVoters.remove(interaction.user)

		embed = Embed(description="Your vote has been saved.", color=constants.colors["gray"])
		embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
		await interaction.response.send_message(embed=embed, ephemeral=True)

	@button(label="Vote skip", style=ButtonStyle.gray)
	async def skipVote(self, button: Button, interaction: Interaction):
		if interaction.user not in self.allVotes:
			self.allVotes.append(interaction.user)
			await self.send_vote_confirmation(interaction, "skip", True)
		elif interaction.user not in self.skipVoters:
			await self.send_vote_confirmation(interaction, "skip", False)

		if interaction.user in self.longVoters: self.longVoters.remove(interaction.user)
		if interaction.user in self.shortVoters: self.shortVoters.remove(interaction.user)
		if interaction.user not in self.skipVoters: self.skipVoters.append(interaction.user)

		embed = Embed(description="Your vote has been saved.", color=constants.colors["gray"])
		embed.set_author(name="Cope consensus trading", icon_url=static_storage.cope)
		await interaction.response.send_message(embed=embed, ephemeral=True)

	async def send_vote_confirmation(self, interaction, side, isInitialVote):
		if isInitialVote:
			allVotes = [e.mention for e in self.allVotes]
			if self.logChannel is not None:
				try: await self.logChannel.send(content=f"{interaction.user.mention} voted to {side}")
				except: pass
			votesSummaryText = ", ".join(allVotes[-20:])
			if len(allVotes) == 21:
				votesSummaryText = ", ".join(allVotes[-21:-1]) + " and " + allVotes[-1]
			elif len(allVotes) > 21:
				votesSummaryText += f" and {len(allVotes) - 20} others"
			votesSummaryText += " voted so far."
			embed = interaction.message.embeds[0]
			embed.description = votesSummaryText
			await interaction.message.edit(embed=embed)
			# await interaction.edit_original_message(embed=embed)
			try: pass
			except: pass
		else:
			if self.logChannel is not None:
				try: await self.logChannel.send(content=f"{interaction.user.mention} changed their vote to {side}")
				except: pass