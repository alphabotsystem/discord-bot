from os import environ
from json import loads, load
from random import choice
from asyncio import CancelledError
from traceback import format_exc

from discord.commands import slash_command, Option
from discord.errors import NotFound

from google.cloud.firestore import Increment

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.transport.grpc import secure_authorized_channel
from google.assistant.embedded.v1alpha2 import embedded_assistant_pb2, embedded_assistant_pb2_grpc

from helpers import constants
from assets import static_storage
from assets import jokes

from commands.base import BaseCommand


class AskCommand(BaseCommand):
	def __init__(self, bot, create_request, database, logging):
		super().__init__(bot, create_request, database, logging)

		assistantCredentials = Credentials(token=None, **loads(environ["GOOGLE_ASSISTANT_OAUTH"]))
		http_request = Request()
		assistantCredentials.refresh(http_request)
		self.grpc_channel = secure_authorized_channel(assistantCredentials, http_request, "embeddedassistant.googleapis.com")

	@slash_command(name="ask", description="Look up definitions, wikipedia articles, and get answers to many other questions.")
	async def assistant(
		self,
		ctx,
		question: Option(str, "Question you want to ask.", name="question")
	):
		try:
			request = await self.create_request(ctx)
			if request is None: return

			if len(question) > 500: return
			response = await self.bot.loop.run_in_executor(None, self.process_reply, question, request.guildProperties.get("settings", {}).get("assistant", {}).get("enabled", True))

			ephemeral = not request.guildProperties.get("settings", {}).get("assistant", {}).get("enabled", True)

			if response is not None:
				try: await ctx.respond(content=response, ephemeral=ephemeral)
				except NotFound: pass
			else:
				try: await ctx.respond(content="Sorry, I can't help you with that.", ephemeral=ephemeral)
				except NotFound: pass

			await self.database.document("discord/statistics").set({request.snapshot: {"alpha": Increment(1)}}, merge=True)

		except CancelledError: pass
		except Exception:
			print(format_exc())
			if environ["PRODUCTION"]: self.logging.report_exception(user=f"{ctx.author.id} {ctx.guild.id if ctx.guild is not None else -1}: /ask {question}")
			await self.unknown_error(ctx)

	def process_reply(self, question, hasPermissions):
		response = self.funnyReplies(question.lower())
		if response is not None: return response
		with GoogleAssistant("en-US", "nlc-bot-36685-nlc-bot-9w6rhy", "Alpha", self.grpc_channel, 60 * 3 + 5) as assistant:
			try: response, response_html = assistant.assist(text_query=question)
			except: return None

			if response	is not None and response != "":
				if "Here are some things you can ask for:" in response:
					return "You can learn more about Alpha.bot's features at https://www.alpha.bot/features"
				elif any(trigger in response for trigger in constants.badPunTrigger):
					return f"Here's a pun that might make you laugh :smile:\n{choice(jokes.jokes)}"
				else:
					for override in constants.messageOverrides:
						for trigger in constants.messageOverrides[override]:
							if trigger.lower() in response.lower():
								return override
					return " ".join(response.replace("Google Assistant", "Alpha.bot").replace("Google", "Alpha.bot").split())
			else:
				return None

	def funnyReplies(self, raw):
		for response in constants.funnyReplies:
			for trigger in constants.funnyReplies[response]:
				if raw == trigger: return response
		return None


class GoogleAssistant(object):
	"""Sample Assistant that supports text based conversations.
	Args:
	  language_code: language for the conversation.
	  device_model_id: identifier of the device model.
	  device_id: identifier of the registered device instance.
	  display: enable visual display of assistant response.
	  channel: authorized gRPC channel for connection to the
		Google Assistant API.
	  deadline_sec: gRPC deadline in seconds for Google Assistant API call.
	"""

	def __init__(self, language_code, device_model_id, device_id, channel, deadline_sec):
		self.language_code = language_code
		self.device_model_id = device_model_id
		self.device_id = device_id
		self.conversation_state = None
		# Force reset of first conversation.
		self.is_new_conversation = True
		self.assistant = embedded_assistant_pb2_grpc.EmbeddedAssistantStub(
			channel
		)
		self.deadline = deadline_sec

	def __enter__(self):
		return self

	def __exit__(self, etype, e, traceback):
		if e:
			return False

	def assist(self, text_query):
		def iter_assist_requests():
			config = embedded_assistant_pb2.AssistConfig(
				audio_out_config=embedded_assistant_pb2.AudioOutConfig(
					encoding='LINEAR16',
					sample_rate_hertz=16000,
					volume_percentage=0,
				),
				dialog_state_in=embedded_assistant_pb2.DialogStateIn(
					language_code=self.language_code,
					conversation_state=self.conversation_state,
					is_new_conversation=self.is_new_conversation,
				),
				device_config=embedded_assistant_pb2.DeviceConfig(
					device_id=self.device_id,
					device_model_id=self.device_model_id,
				),
				text_query=text_query,
			)
			# Continue current conversation with later requests.
			self.is_new_conversation = False
			req = embedded_assistant_pb2.AssistRequest(config=config)
			yield req

		text_response = None
		html_response = None
		for resp in self.assistant.Assist(iter_assist_requests(),
										  self.deadline):
			if resp.screen_out.data:
				html_response = resp.screen_out.data
			if resp.dialog_state_out.conversation_state:
				conversation_state = resp.dialog_state_out.conversation_state
				self.conversation_state = conversation_state
			if resp.dialog_state_out.supplemental_display_text:
				text_response = resp.dialog_state_out.supplemental_display_text
		return text_response, html_response
