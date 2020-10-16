import json
import threading
import time

import catbot
import mwclient

from ac import Ac

config = json.load(open('config.json', 'r', encoding='utf-8'))
bot = catbot.Bot(config)
t_lock = threading.Lock()
site = mwclient.Site(config['main_site'])


def command_detector(cmd: str, msg: catbot.Message) -> bool:
    if cmd in msg.commands:
        return msg.text.startswith(cmd)
    elif f'{cmd}@{bot.username}' in msg.commands:
        return msg.text.startswith(f'{cmd}@{bot.username}')
    else:
        return False


def record_empty_test(key: str, data_type):
    """
    :param key: Name of the data you want in record file
    :param data_type: Type of the data. For example, if it is trusted user list, data_type will be list.
    :return: Returns a tuple. The first element is the data you asked for. The second is the deserialized record file.
    """
    try:
        rec = json.load(open(config['record'], 'r', encoding='utf-8'))
    except FileNotFoundError:
        record_list, rec = data_type(), {}
        json.dump({key: record_list}, open(config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    else:
        if key in rec.keys():
            record_list = rec[key]
        else:
            record_list = data_type()

    return record_list, rec


def log(text):
    bot.send_message(config['log_channel'], text=text, parse_mode='HTML')


def start_cri(msg: catbot.Message) -> bool:
    return command_detector('start', msg) and msg.chat.type == 'private'


def start(msg: catbot.Message):
    ac_list, rec = record_empty_test('ac', list)
    for entry_dict in ac_list:
        entry = Ac.from_dict(entry_dict)
        if entry.telegram_id == msg.from_.id:
            break
    else:
        new_entry = Ac(msg.from_.id)
        ac_list.append(new_entry.to_dict())
        rec['ac'] = ac_list
        json.dump(rec, open(config['record'], 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    bot.send_message(msg.chat.id, text=config['messages']['start'])


def policy_cri(msg: catbot.Message) -> bool:
    return command_detector('policy', msg)


def policy(msg: catbot.Message):
    bot.send_message(msg.chat.id, text=config['messages']['policy'])


def confirm_cri(msg: catbot.Message) -> bool:
    return command_detector('confirm', msg) and msg.chat.type == 'private'


def confirm(msg: catbot.Message):
    user_input_token = msg.text.split()
    if len(user_input_token) == 1:
        bot.send_message(msg.chat.id, text=config['messages']['confirm_prompt'], parse_mode='HTML')
        return

    wikipedia_username = '_'.join(user_input_token[1:])
    with t_lock:
        ac_list, rec = record_empty_test('ac', list)

        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id == msg.from_.id:
                if entry.confirmed:
                    bot.send_message(msg.chat.id,
                                     text=config['messages']['confirm_already'].format(
                                         wp_name=entry.wikimedia_username))
                    return
                else:
                    user_record_index = i
                    break
            elif entry.wikimedia_username == wikipedia_username and (entry.confirmed or entry.confirming):
                bot.send_message(msg.chat.id, text=config['messages']['confirm_conflict'])
                return
        else:
            user_record_index = len(ac_list)
            entry = Ac(msg.from_.id)

        global_user_info_query = site.api(**{
            "action": "query",
            "format": "json",
            "meta": "globaluserinfo",
            "utf8": 1,
            "formatversion": "2",
            "guiuser": wikipedia_username,
            "guiprop": "merged"
        })

        if 'missing' in global_user_info_query['query']['globaluserinfo'].keys():
            bot.send_message(msg.chat.id, text=config['messages']['confirm_user_not_found'].format(
                name=wikipedia_username))
            return

        global_user_info = global_user_info_query['query']['globaluserinfo']['merged']
        for local_user in global_user_info:
            if local_user['wiki'] in config['wiki_list'] and local_user['wiki']['editcount'] >= 50 and \
                    time.mktime(time.strptime(local_user['registration'], '%Y-%m-%dT%H:%M:%SZ')) > 7 * 86400:
                entry.confirming = True
                h = hash(time.time())
                button = catbot.InlineKeyboardButton(config['messages']['confirm_button'], callback_data=f'confirm_{h}')
                keyboard = catbot.InlineKeyboard([[button]])
                bot.send_message(msg.chat.id, text=config['messages']['confirm_wait'].format(
                    name=wikipedia_username, h=h), reply_markup=keyboard)
            else:
                bot.send_message(msg.chat.id, text=config['messages']['confirm_ineligible'])
                return

        entry.wikimedia_username = wikipedia_username
        ac_list[user_record_index] = entry.to_dict()
        rec['ac'] = ac_list
        json.dump(rec, open(config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
