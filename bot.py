from telethon import TelegramClient, events, Button, tl
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
import config
from loguru import logger
import requests
import re
import pandas as pd
import math

bot = TelegramClient('mysurfbot', config.API_ID, config.API_HASH).start(bot_token=config.BOT_TOKEN)

# Function to validate the time in 24-hour format
def isValidTime(time):  
    regex = "^([01]?[0-9]|2[0-3]):[0-5][0-9]$";
    p = re.compile(regex);
 
    if (time == ""):
        return False
    m = re.search(p, time)
    if m is None:
        return False
    else:
        return True

async def send_update(sender_id, spot_id, spot_name, surf_height, ndays, cron_time):
    r = requests.get(f'http://services.surfline.com/kbyg/spots/forecasts/conditions?spotId={spot_id}&days={ndays}').json()
    df = pd.json_normalize(r['data']['conditions'])
    df['time'] = pd.to_datetime(df['timestamp'], unit='s').dt.date
    filtered_df = df[(df['am.maxHeight']>=surf_height) | (df['pm.maxHeight']>=surf_height)].copy()
    if len(filtered_df)>0:
        filtered_df['AM height'] = filtered_df['am.minHeight'].astype(str) + '-' + filtered_df['am.maxHeight'].astype(str)
        filtered_df['PM height'] = filtered_df['pm.minHeight'].astype(str) + '-' + filtered_df['pm.maxHeight'].astype(str)

        await bot.send_message(sender_id, "🏄 Surf's up!!! 🏄")
        await bot.send_message(sender_id, filtered_df[['time', 'AM height', 'PM height']].to_markdown(), parse_mode='md')
        await bot.send_message(sender_id, f"Check the full forecast: https://www.surfline.com/surf-report/{spot_name}/{spot_id}") 
        logger.info(f'Sent update to {sender_id} for {spot_name}.')
        
@bot.on(events.NewMessage(pattern='/delete'))
async def delete(event):
    sender = await event.get_sender()
    sender_id = sender.id
    try:
        scheduler.remove_job(sender_id)
        await event.respond('Subscription deleted! You can set a new one with /start')
        logger.info(f'Subscription deleted for {sender_id}.')
    except:
        await event.respond('No subscription found! You can set a new one with /start')
    raise events.StopPropagation
    
@bot.on(events.NewMessage(pattern='/settings'))
async def get_settings(event):  
    sender = await event.get_sender()
    sender_id = sender.id
    
    data = scheduler.get_job(sender_id)
    if data:
        message = f'Spot: {data.args[2]}\nDays forecast: {data.args[4]}\nMinimum max surf height: {data.args[3]}\nDaily update time: {data.args[5]}'
        await event.respond(message)
    else:
        await event.respond('No subscription found! You can set a new one with /start')
    raise events.StopPropagation
    
@bot.on(events.NewMessage(pattern='/start'))
async def register_spot(event):
    chat_id = event.message.chat.id    
    sender = await event.get_sender()
    sender_id = sender.id
    
    try:
        async with bot.conversation(chat_id) as conv:
            
            def my_press_event(user_id):
                return events.CallbackQuery(func=lambda e: e.sender_id == user_id)

            await conv.send_message("Welcome to SurfAlert. We'll register your spot for daily surf updates.")
            await conv.send_message("Time to start the registration process. You can cancel the registration process at any time with /cancel.")
            await conv.send_message("What is the name of your spot you'd like to monitor?") 
            query = (await conv.get_response()).text
            
            url = f'https://services.surfline.com/search/site?q={query}&querySize=10&suggestionSize=10'
            response = requests.get(url).json()


            hits = response[0]['hits']['hits']
            suggestions = [i for i in response[0]['suggest']['spot-suggest'][0]['options'] if not i['_source']['name'] in [val['_source']['name'] for val in hits]]
            results = hits[:10] + suggestions[:(10 - len(hits[:10]))]
            buttons_list = [Button.inline(str(n) + '.' + i['_source']['name'], bytes('findspot_' + i['_source']['name'], encoding="utf-8")) for n, i in enumerate(results, start=1)]
        
            if (math.ceil(len(buttons_list)/2) % 2) == 0:
                await conv.send_message('Pick one from this grid', buttons=[[i, buttons_list[5+n]] for n, i in enumerate(buttons_list[:5])]+[[Button.inline('Cancel', b'findspot_cancel')]])
            else:
                await conv.send_message('Pick one from this grid', buttons=[[i, buttons_list[math.ceil(len(buttons_list)/2)+n]] if math.ceil(len(buttons_list)/2)+n < len(buttons_list) else [i] for n, i in enumerate(buttons_list[:math.ceil(len(buttons_list)/2)])]+[[Button.inline('Cancel', b'findspot_cancel')]])

            tasks = [conv.wait_event(my_press_event(sender_id)), conv.get_response()]
            done, pendind = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            event = done.pop().result()

            while True:
                if type(event) is events.callbackquery.CallbackQuery.Event:
                    selected = event.data.decode('utf-8')
                    if selected == 'findspot_cancel':
                        await event.edit('Registration cancelled.')
                        await conv.cancel_all()
                        break
                    else:
                        await event.edit('Selected spot: ' + selected.split('_')[1])
                        spot = [i for i in results if i['_source']['name'] == selected.split('_')[1]][0]
                        await conv.send_message('Explore your spot')
                        await conv.send_message(spot['_source']['href'])

                        await conv.send_message('Set minimum surf height in meters, eg. 0.9')
                        height = (await conv.get_response()).text
                        while not height.replace('.','',1).isdigit():
                            await conv.send_message("Not a number! Try again or /cancel")
                            height = (await conv.get_response()).text

                        await conv.send_message('For how many days would you like to retrieve the forecast (1-5)?')
                        ndays = (await conv.get_response()).text
                        while not (ndays.isnumeric()) or not (int(ndays) in range(1,6)):
                            await conv.send_message("Please submit a number from 1 to 5! Try again or /cancel")
                            ndays = (await conv.get_response()).text

                        await conv.send_message('Final question: at what time do you want to receive your daily update (24-hour notation, eg. 08:30 or 15:15)?')
                        cron_time = (await conv.get_response()).text
                        while not isValidTime(cron_time):
                            await conv.send_message("Not the correct time format! Try again or /cancel")
                            cron_time = (await conv.get_response()).text

                        scheduler.add_job(send_update, 'cron', hour=int(cron_time.split(':')[0]), minute=int(cron_time.split(':')[1]), id=f'{chat_id}', args=[sender_id, spot['_id'], spot['_source']['name'], float(height), int(ndays), cron_time], replace_existing=True)
                        logger.info(f'Spot {spot["_source"]["name"]} registered for {sender_id}.')
                        await conv.send_message(f"Thanks! You will receive a daily message at {cron_time} if the maximum surf height at {spot['_source']['name']} hits {height} meters the coming {ndays} days.\n\nYou can always delete your registration with /delete or check your settings with /settings. Note that you can only register for one spot, new registrations will overwrite existing ones.")
                        
                        break
                elif type(event) is tl.patched.Message: 
                    message = event.text
                    await conv.send_message('Select your spot or /cancel')
                    tasks = [conv.wait_event(my_press_event(sender_id)), conv.get_response()]
                    done, pendind = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    event = done.pop().result()
            
    except asyncio.TimeoutError:
        await event.respond('Registration timed out.')
    except asyncio.CancelledError:
        pass

@bot.on(events.NewMessage(pattern='/cancel'))
async def cancel_handler(event):
    client = event.client
    # set exclusive=False so we can still create a conversation, even when there's an existing (exclusive) one.
    async with event.client.conversation(await event.get_chat(), exclusive=False) as conv:
        await conv.cancel_all()
    await event.respond(f'Registration cancelled.', buttons=None)

try:
    logger.info('Starting bot...')
    logger.info('(Press Ctrl+C to stop the bot)')
    logger.info('Starting scheduler...')
    scheduler = AsyncIOScheduler(job_defaults={'misfire_grace_time': 15*60})
    scheduler.add_jobstore('sqlalchemy', url='sqlite:///' + config.SCHEDULER_DB)
    scheduler.start()

    bot.parse_mode = 'html'    
    """Start the bot."""
    bot.run_until_disconnected()
finally:
    logger.info('Closing bot...')
    scheduler.shutdown(wait=False)
    bot.disconnect()
