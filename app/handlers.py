import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import CommandHandler, CallbackQueryHandler
from database.database import *
from app.menu import *

def echo(update, context):
    user_id = update.message.from_user.id
    message_text = update.message.text

    if context.user_data.get(user_id, False):
        # Send the feedback to the target group
        TARGET_GROUP_CHAT_ID = -953584245 #os.environ.get('-953584245')
        feedback_message = f"Feedback/Contribution from:\nUser ID: {update.message.from_user.id}\nUsername: @{update.message.from_user.username}\nName: {update.message.from_user.first_name} {update.message.from_user.last_name}\nMessage ID: {update.message.message_id}\n\n{message_text}"
        context.bot.send_message(chat_id=TARGET_GROUP_CHAT_ID, text=feedback_message)

        update.message.reply_text('وصلتنا رسالتك، شكرًا لاهتمامك! - Your message is well received!')
        update.message.reply_text('/menu_ar\n/menu_en')
        # await self.show(update)
        context.user_data[user_id] = False

    else:
        if message_text == "عودة":
            show_ar(update)
        elif message_text == 'Back':
            show_en(update)

        elif message_text in get_categories(data_ar):
            values: Dict[str, str] = get_values(data_ar, message_text)
            buttons = [[KeyboardButton(key)] for key in values.keys()]
            reply_markup = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
            update.message.reply_text(f'هاك الأسئلة الشائعة بخصوص {message_text}', reply_markup=reply_markup)

        elif message_text in get_categories(data_en):
            values: Dict[str, str] = get_values(data_en, message_text)
            buttons = [[KeyboardButton(key)] for key in values.keys()]
            reply_markup = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
            update.message.reply_text(f'here most common questions about {message_text}',
                                            reply_markup=reply_markup)

        elif has_question(data_ar, message_text):
            answer = get_answer(data_ar, message_text)
            update.message.reply_text(answer)
            update_enquiry_user(str(user_id))

        elif has_question(data_en, message_text):
            answer = get_answer(data_en, message_text)
            update.message.reply_text(answer)
            update_enquiry_user(str(user_id))

        elif update.message.reply_to_message and "Feedback" in update.message.reply_to_message.text:
            # Extract the user ID and message ID from the feedback message using regex pattern
            pattern = r"(?s)User ID:\s*(\d+)\s*\n.*?Message ID:\s*(\d+)\s*\n"
            match = re.search(pattern, update.message.reply_to_message.text)

            if match:
                user_id = int(match.group(1))
                message_id = int(match.group(2))
                if "Send" in message_text:
                    reply_text = message_text.replace("Send", "").strip()
                    context.send_message(context.bot, user_id, reply_text, message_id)
            else:
                update.message.reply_text("Could not extract user ID and message ID from the feedback message.")

        else:
            context.user_data[user_id] = False

def set_commands_handler(dp):#once

    dp.add_handler(CommandHandler('start', start_command))
    #dp.add_handler(CommandHandler('contribute', contribute_command))
    dp.add_handler(CommandHandler('menu_en', menu_en_command))
    dp.add_handler(CommandHandler('menu_ar', menu_ar_command))
    dp.add_handler(CommandHandler('feedback', feedback_command))
    #dp.add_handler(CommandHandler('question', feedback_command))
    dp.add_handler(CommandHandler('get_userid', get_userid_command))

    dp.add_handler(CallbackQueryHandler(language_callback))

    load_data()


def help_command_handler(update, context):
    """Send a message when the command /help is issued."""
    update.message.reply_text("Type /start to register to the service")


def start_command(update, context):
    add_new_user(update.message.from_user.id)

    keyboard = [
              [InlineKeyboardButton("العربية", callback_data='arabic')],
              [InlineKeyboardButton("English", callback_data='english')]
          ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text('Please choose your language:\nيرجى اختيار لغتك:', reply_markup=reply_markup)#todo changing language can wait :)

def language_callback(update, context):
    query = update.callback_query
    language = query.data
    if language == 'arabic':
        query.answer()
        query.message.reply_text(read_txt_file('FAQ/AR/hello.txt'))
        query.message.reply_text('لتصفح الأسئلة الشائعة:\n/menu_ar')
        #show_ar(update)

    elif language == 'english':
        query.answer()
        query.message.reply_text(read_txt_file('FAQ/EN/hello.txt'))
        query.message.reply_text('Browse FAQ :\n/menu_en')
        #show_en(update)




def contribute_command(update, context):
    pass

def feedback_command(update, context):
    user_id = update.message.from_user.id
    context.user_data[user_id] = True
    reply_markup = ReplyKeyboardRemove()
    update.message.reply_text('Your next message is your feedback - أرسل ملاحظاتك من فضلك', reply_markup=reply_markup)

def get_userid_command(update, context):
    user_id = update.message.from_user.id
    update.message.reply_text(f"Your user ID is: {user_id}")

def menu_ar_command(update, context):
    show_ar(update)

def menu_en_command(update, context):
    show_en(update)

