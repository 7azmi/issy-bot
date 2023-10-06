import telegram
from telegram import Update
from telegram import ReplyKeyboardMarkup, KeyboardButton
import statistics

data_en = {}
data_ar = {}


def load_data():
    data_en['Registrations'] = extract_qa('FAQ/EN/registration.txt')
    data_en['Visa'] = extract_qa('FAQ/EN/visa.txt')
    data_en['Student life'] = extract_qa('FAQ/EN/studentLife.txt')
    data_en['Services'] = extract_qa('FAQ/EN/services.txt')
    data_en['Donate'] = extract_qa('FAQ/EN/donation.txt')
    data_en['Faculties'] = extract_qa('FAQ/EN/YSAG.txt')

    data_ar['التسجيل في الجامعة'] = extract_qa('FAQ/AR/registration.txt')
    data_ar['التأشيرة'] = extract_qa('FAQ/AR/visa.txt')
    data_ar['حياة الطالب'] = extract_qa('FAQ/AR/studentLife.txt')
    data_ar['الخدمات'] = extract_qa('FAQ/AR/services.txt')
    data_ar['تبرع'] = extract_qa('FAQ/AR/donation.txt')
    data_ar['الكليات'] = extract_qa('FAQ/AR/YSAG.txt')


def show_ar(update: Update) -> None:
    buttons = [[KeyboardButton(key)] for key in get_categories(data_ar)]
    reply_markup = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    update.message.reply_text('إليك قائمة الفئات', reply_markup=reply_markup)


def show_en(update: Update) -> None:
    buttons = [[KeyboardButton(key)] for key in get_categories(data_en)]
    reply_markup = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    update.message.reply_text('Here is category list:', reply_markup=reply_markup)


def extract_qa(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    qa_dict = {}
    current_question = ''
    current_answer = ''
    is_question = False
    is_answer = False

    for line in lines:
        if line.startswith('Q:'):
            if current_question and current_answer:
                qa_dict[current_question.strip()] = current_answer.strip()
            current_question = line[3:].strip()
            is_question = True
            is_answer = False
            current_answer = ''
        elif line.startswith('A:'):
            is_answer = True
            is_question = False
            current_answer = line[3:].strip()
        else:
            if is_question:
                current_question += '\n' + line.strip()
            elif is_answer:
                current_answer += '\n' + line.strip()

    if current_question and current_answer:
        qa_dict[current_question.strip()] = current_answer.strip()

    return qa_dict


def read_txt_file(file_path, encoding='utf-8'):
    try:
        with open(file_path, 'r', encoding=encoding) as file:
            file_contents = file.read()
        return file_contents
    except UnicodeDecodeError:
        print(f"Unable to decode the file using encoding: {encoding}")
        return None

# Usage example:
# file_contents = read_txt_file("your_file_path.txt")



def get_categories(data):
    return data.keys()


def get_values(data, category):
    return data[category]


def has_question(data, question):
    return any(question in values.keys() for values in data.values())


def get_answer(data, question):
    for values in data.values():
        if question in values.keys():
            return values[question]
    return None


