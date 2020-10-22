import json
import threading
import time
from calendar import timegm

import catbot
import mwclient

from ac import Ac

config = json.load(open('config_test.json', 'r', encoding='utf-8'))
bot = catbot.Bot(config)
t_lock = threading.Lock()
site = mwclient.Site(config['main_site'], reqs=bot.proxy_kw)


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
    return command_detector('/start', msg) and msg.chat.type == 'private'


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
    return command_detector('/policy', msg)


def policy(msg: catbot.Message):
    bot.send_message(msg.chat.id, text=config['messages']['policy'])


def confirm_cri(msg: catbot.Message) -> bool:
    return command_detector('/confirm', msg) and msg.chat.type == 'private'


def confirm(msg: catbot.Message):
    user_input_token = msg.text.split()
    if len(user_input_token) == 1:
        bot.send_message(msg.chat.id, text=config['messages']['confirm_prompt'], parse_mode='HTML')
        return

    wikimedia_username = '_'.join(user_input_token[1:])
    bot.send_message(msg.chat.id, text=config['messages']['confirm_checking'])
    global_user_info_query = site.api(**{
        "action": "query",
        "format": "json",
        "meta": "globaluserinfo",
        "utf8": 1,
        "formatversion": "2",
        "guiuser": wikimedia_username,
        "guiprop": "merged"
    })

    if 'missing' in global_user_info_query['query']['globaluserinfo'].keys():
        bot.send_message(msg.chat.id, text=config['messages']['confirm_user_not_found'].format(
            name=wikimedia_username))
        return

    global_user_info = global_user_info_query['query']['globaluserinfo']['merged']
    for local_user in global_user_info:
        if local_user['wiki'] in config['wiki_list'] and local_user['editcount'] >= 50 and \
                time.time() - timegm(time.strptime(local_user['registration'], '%Y-%m-%dT%H:%M:%SZ')) > 7 * 86400:
            break
    else:
        bot.send_message(msg.chat.id, text=config['messages']['confirm_ineligible'])
        return

    with t_lock:
        ac_list, rec = record_empty_test('ac', list)

        exist = False
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id == msg.from_.id:
                if entry.confirmed:
                    bot.send_message(msg.chat.id,
                                     text=config['messages']['confirm_already'].format(
                                         wp_name=entry.wikimedia_username))
                    return
                else:
                    entry_index = i
                    exist = True
            elif entry.wikimedia_username == wikimedia_username and (entry.confirmed or entry.confirming):
                bot.send_message(msg.chat.id, text=config['messages']['confirm_conflict'])
                return

        if not exist:
            entry_index = len(ac_list)
            entry = Ac(msg.from_.id)
            ac_list.append(entry)

        entry.confirming = True
        entry.wikimedia_username = wikimedia_username
        ac_list[entry_index] = entry.to_dict()
        rec['ac'] = ac_list
        json.dump(rec, open(config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    h = hash(time.time())
    button = catbot.InlineKeyboardButton(config['messages']['confirm_button'], callback_data=f'confirm_{h}')
    keyboard = catbot.InlineKeyboard([[button]])
    bot.send_message(msg.chat.id, text=config['messages']['confirm_wait'].format(site=config['main_site'],
                                                                                 name=wikimedia_username,
                                                                                 h=h),
                     parse_mode='HTML', disable_web_page_preview=True, reply_markup=keyboard)


def confirm_button_cri(query: catbot.CallbackQuery) -> bool:
    return query.data.startswith('confirm') and query.msg.chat.type == 'private'


def confirm_button(query: catbot.CallbackQuery):
    confirm_token = query.data.split('_')[1]
    bot.answer_callback_query(callback_query_id=query.id)
    with t_lock:
        ac_list, rec = record_empty_test('ac', list)
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id != query.from_.id:
                continue
            if entry.confirmed:
                bot.send_message(query.msg.chat.id, text=config['messages']['confirm_already'].format(
                    wp_name=entry.wikimedia_username))
                bot.edit_message(query.msg.chat.id, query.msg.id, text=query.msg.html_formatted_text, parse_mode='HTML',
                                 disable_web_page_preview=True)
                return
            if not entry.confirming:
                bot.send_message(query.msg.chat.id, text=config['messages']['confirm_session_lost'])
                bot.edit_message(query.msg.chat.id, query.msg.id, text=query.msg.html_formatted_text, parse_mode='HTML',
                                 disable_web_page_preview=True)
                return
            else:
                entry_index = i
                break
        else:
            bot.send_message(query.msg.chat.id, text=config['messages']['confirm_session_lost'])
            bot.edit_message(query.msg.chat.id, query.msg.id, text=query.msg.html_formatted_text, parse_mode='HTML',
                             disable_web_page_preview=True)
            return

        try:
            revs = site.Pages[f'User:{entry.wikimedia_username}'].revisions()
            while True:
                rev = next(revs)
                if 0 <= timegm(rev['timestamp']) - query.msg.date <= 180:
                    if rev['user'] != entry.wikimedia_username:
                        continue
                    if confirm_token not in rev['comment']:
                        continue
                    entry.confirmed = True
                    entry.confirming = False
                    bot.send_message(query.msg.chat.id, text=config['messages']['confirm_complete'])
                    break
                else:
                    bot.send_message(query.msg.chat.id, text=config['messages']['confirm_failed'])
                    entry.confirmed = False
                    entry.confirming = False
                    break
        except StopIteration:
            bot.send_message(query.msg.chat.id, text=config['messages']['confirm_failed'])
            entry.confirmed = False
            entry.confirming = False

        ac_list[entry_index] = entry.to_dict()
        rec['ac'] = ac_list
        json.dump(rec, open(config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
        bot.edit_message(query.msg.chat.id, query.msg.id, text=query.msg.html_formatted_text, parse_mode='HTML',
                         disable_web_page_preview=True)

    try:
        if entry.confirmed:
            if entry.restricted_until != 0 and entry.restricted_until <= time.time() + 30:
                bot.lift_restrictions(config['group'], query.from_.id)
            else:
                bot.silence_chat_member(config['group'], query.from_.id, until=entry.restricted_until)
    except catbot.RestrictAdminError:
        pass
    except catbot.InsufficientRightError:
        bot.send_message(config['group'], text=config['messages']['insufficient_right'])


if __name__ == '__main__':
    bot.add_msg_task(start_cri, start)
    bot.add_msg_task(policy_cri, policy)
    bot.add_msg_task(confirm_cri, confirm)
    bot.add_query_task(confirm_button_cri, confirm_button)
    bot.start()
