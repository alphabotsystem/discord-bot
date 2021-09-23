source /run/secrets/alpha-service/key
if [[ $PRODUCTION_MODE == "1" ]]
then
	python app/discord_bot.py
else
	python -u app/discord_bot.py
fi