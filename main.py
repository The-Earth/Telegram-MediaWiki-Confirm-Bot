import json
import threading
import time
from calendar import timegm

import catbot
import mwclient

from ac import Ac

config = json.load(open('config.json', 'r', encoding='utf-8'))
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
    bot.send_message(config['log_channel'], text=text, parse_mode='HTML', disable_web_page_preview=True)


def start_cri(msg: catbot.Message) -> bool:
    return command_detector('/start', msg) and msg.chat.type == 'private'


def start(msg: catbot.Message):
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
        if local_user['editcount'] >= 50 and \
                time.time() - timegm(time.strptime(local_user['registration'], '%Y-%m-%dT%H:%M:%SZ')) > 7 * 86400:
            break
    else:
        bot.send_message(msg.chat.id, text=config['messages']['confirm_ineligible'])
        return

    with t_lock:
        ac_list, rec = record_empty_test('ac', list)

        entry_index = -1
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id == msg.from_.id:
                if entry.confirmed:
                    bot.send_message(msg.chat.id,
                                     text=config['messages']['confirm_already'].format(
                                         wp_name=entry.wikimedia_username))
                    return
                else:
                    if entry.refused:
                        bot.send_message(msg.chat.id, text=config['messages']['confirm_ineligible'])
                        return
                    entry_index = i

            elif entry.wikimedia_username == wikimedia_username and (entry.confirmed or entry.confirming):
                bot.send_message(msg.chat.id, text=config['messages']['confirm_conflict'])
                return
        else:
            if entry_index == -1:
                entry = Ac(msg.from_.id)
                ac_list.append(entry.to_dict())
            entry = Ac.from_dict(ac_list[entry_index])
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
    bot.edit_message(query.msg.chat.id, query.msg.id, text=query.msg.html_formatted_text, parse_mode='HTML',
                     disable_web_page_preview=True)
    with t_lock:
        ac_list, rec = record_empty_test('ac', list)
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id != query.from_.id:
                continue
            if entry.confirmed:
                bot.send_message(query.msg.chat.id, text=config['messages']['confirm_already'].format(
                    wp_name=entry.wikimedia_username))
                return
            if entry.confirming:
                entry_index = i
                break
        else:
            bot.send_message(query.msg.chat.id, text=config['messages']['confirm_session_lost'])
            return

        try:
            revs = site.Pages[f'User:{entry.wikimedia_username}'].revisions()
            while True:
                rev = next(revs)
                if 0 <= timegm(rev['timestamp']) - query.msg.date <= 180:
                    if rev['user'].replace(' ', '_') != entry.wikimedia_username:
                        continue
                    if confirm_token not in rev['comment']:
                        continue
                    entry.confirmed = True
                    entry.confirming = False
                    entry.confirmed_time = time.time()
                    break
                else:
                    entry.confirmed = False
                    entry.confirming = False
                    break
        except StopIteration:
            entry.confirmed = False
            entry.confirming = False

        ac_list[entry_index] = entry.to_dict()
        rec['ac'] = ac_list
        json.dump(rec, open(config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    try:
        if entry.confirmed:
            log(config['messages']['confirm_log'].format(tg_id=entry.telegram_id, wp_id=entry.wikimedia_username,
                                                         site=config['main_site']))
            bot.send_message(query.msg.chat.id, text=config['messages']['confirm_complete'])
            if entry.restricted_until <= time.time() + 35:
                bot.lift_restrictions(config['group'], query.from_.id)
            else:
                bot.silence_chat_member(config['group'], query.from_.id, until=entry.restricted_until)
                bot.send_message(config['group'],
                                 text=config['messages']['restore_silence'].format(tg_id=entry.telegram_id),
                                 parse_mode='HTML')
        else:
            bot.send_message(query.msg.chat.id, text=config['messages']['confirm_failed'])
    except catbot.RestrictAdminError:
        pass
    except catbot.InsufficientRightError:
        bot.send_message(config['group'], text=config['messages']['insufficient_right'])
    except catbot.UserNotFoundError:
        pass


def deconfirm_cri(msg: catbot.Message) -> bool:
    return command_detector('/deconfirm', msg) and msg.chat.type == 'private'


def deconfirm(msg: catbot.Message):
    button = catbot.InlineKeyboardButton(config['messages']['deconfirm_button'], callback_data='deconfirm')
    keyboard = catbot.InlineKeyboard([[button]])
    bot.send_message(msg.chat.id, text=config['messages']['deconfirm_prompt'], reply_markup=keyboard)


def deconfirm_button_cri(query: catbot.CallbackQuery) -> bool:
    return query.data == 'deconfirm' and query.msg.chat.type == 'private'


def deconfirm_button(query: catbot.CallbackQuery):
    bot.answer_callback_query(query.id)
    try:
        user_chat = bot.get_chat_member(config['group'], query.from_.id)
    except catbot.UserNotFoundError:
        restricted_until = 0
    else:
        if user_chat.status == 'restricted':
            restricted_until = user_chat.until_date
            if restricted_until == 0:
                restricted_until = -1  # Restricted by bot, keep entry.restricted_until unchanged later
        else:
            restricted_until = 0

    with t_lock:
        ac_list, rec = record_empty_test('ac', list)
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id == query.from_.id:
                if entry.confirmed:
                    entry.confirmed = False
                    ac_list[i] = entry.to_dict()
                    if restricted_until != -1:
                        entry.restricted_until = restricted_until
                    break
                else:
                    bot.send_message(query.msg.chat.id, text=config['messages']['deconfirm_not_confirmed'])
                    return
        else:
            bot.send_message(query.msg.chat.id, text=config['messages']['deconfirm_not_confirmed'])
            return

        rec['ac'] = ac_list
        json.dump(rec, open(config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    log(config['messages']['deconfirm_log'].format(tg_id=entry.telegram_id, wp_id=entry.wikimedia_username,
                                                   site=config['main_site']))
    bot.send_message(query.msg.chat.id, text=config['messages']['deconfirm_succ'])

    if not entry.whitelist_reason:
        try:
            bot.silence_chat_member(config['group'], query.from_.id)
        except catbot.InsufficientRightError:
            bot.send_message(config['group'], text=config['messages']['insufficient_right'])
        except catbot.RestrictAdminError:
            pass
        except catbot.UserNotFoundError:
            pass


def new_member_cri(msg: catbot.Message) -> bool:
    return msg.chat.id == config['group'] and hasattr(msg, 'new_chat_members')


def new_member(msg: catbot.Message):
    user = msg.new_chat_members[0]
    user_chat = bot.get_chat_member(config['group'], user.id)
    if user_chat.is_bot:
        return
    if user_chat.status == 'restricted':
        restricted_until = user_chat.until_date
        if restricted_until == 0:
            restricted_until = -1  # Restricted by bot, keep entry.restricted_until unchanged later
    elif user_chat.status == 'creator' or user_chat.status == 'administrator':
        return
    else:
        restricted_until = 0

    try:
        bot.silence_chat_member(config['group'], user.id)
    except catbot.InsufficientRightError:
        bot.send_message(config['group'], text=config['messages']['insufficient_right'])
        return

    with t_lock:
        ac_list, rec = record_empty_test('ac', list)
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id == user.id:
                user_index = i
                break
        else:
            entry = Ac(msg.from_.id)
            ac_list.append(entry)
            user_index = -1

        if restricted_until != -1:
            entry.restricted_until = restricted_until
        ac_list[user_index] = entry.to_dict()
        rec['ac'] = ac_list
        json.dump(rec, open(config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    try:
        if entry.confirmed or entry.whitelist_reason:
            if entry.restricted_until <= time.time() + 35:
                bot.lift_restrictions(config['group'], user.id)
            else:
                bot.silence_chat_member(config['group'], user.id, until=entry.restricted_until)
                bot.send_message(config['group'],
                                 text=config['messages']['restore_silence'].format(tg_id=entry.telegram_id),
                                 parse_mode='HTML')
        else:
            bot.send_message(config['group'], text=config['messages']['new_member_hint'], reply_to_message_id=msg.id)
    except catbot.InsufficientRightError:
        pass


def add_whitelist_cri(msg: catbot.Message) -> bool:
    return command_detector('/add_whitelist', msg)


def add_whitelist(msg: catbot.Message):
    adder = bot.get_chat_member(config['group'], msg.from_.id)
    if not (adder.status == 'creator' or adder.status == 'administrator'):
        return

    user_input_token = msg.text.split()
    if msg.reply:
        whitelist_id = msg.reply_to_message.from_.id
        if len(user_input_token) > 1:
            reason = ' '.join(user_input_token[1:])
        else:
            reason = 'whitelisted'
    else:
        if len(user_input_token) < 2:
            bot.send_message(msg.chat.id, text=config['messages']['add_whitelist_prompt'], reply_to_message_id=msg.id)
            return
        whitelist_id = int(user_input_token[1])
        if len(user_input_token) > 2:
            reason = ' '.join(user_input_token[2:])
        else:
            reason = 'whitelisted'

    with t_lock:
        ac_list, rec = record_empty_test('ac', list)
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id == whitelist_id:
                entry.whitelist_reason = reason
                ac_list[i] = entry.to_dict()
                break
        else:
            entry = Ac(whitelist_id)
            entry.whitelist_reason = reason
            ac_list.append(entry.to_dict())

        rec['ac'] = ac_list
        json.dump(rec, open(config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    log(config['messages']['add_whitelist_log'].format(adder=adder.name, tg_id=whitelist_id, reason=reason))
    bot.send_message(msg.chat.id, text=config['messages']['add_whitelist_succ'].format(tg_id=whitelist_id),
                     reply_to_message_id=msg.id)

    try:
        if entry.restricted_until <= time.time() + 35:
            bot.lift_restrictions(config['group'], whitelist_id)
        else:
            bot.silence_chat_member(config['group'], whitelist_id, until=entry.restricted_until)
            bot.send_message(config['group'],
                             text=config['messages']['restore_silence'].format(tg_id=entry.telegram_id),
                             parse_mode='HTML')
    except catbot.RestrictAdminError:
        pass
    except catbot.InsufficientRightError:
        bot.send_message(msg.chat.id, text=config['messages']['insufficient_right'])
    except catbot.UserNotFoundError:
        pass


def remove_whitelist_cri(msg: catbot.Message) -> bool:
    return command_detector('/remove_whitelist', msg)


def remove_whitelist(msg: catbot.Message):
    try:
        remover = bot.get_chat_member(config['group'], msg.from_.id)
    except catbot.UserNotFoundError:
        return
    if not (remover.status == 'creator' or remover.status == 'administrator'):
        return

    user_input_token = msg.text.split()
    if msg.reply:
        whitelist_id = msg.reply_to_message.from_.id
    else:
        if len(user_input_token) < 2:
            bot.send_message(msg.chat.id, text=config['messages']['add_whitelist_prompt'], reply_to_message_id=msg.id)
            return
        whitelist_id = int(user_input_token[1])

    try:
        whitelist_user = bot.get_chat_member(config['group'], whitelist_id)
    except catbot.UserNotFoundError:
        restricted_until = 0
    else:
        if whitelist_user.status == 'restricted':
            restricted_until = whitelist_user.until_date
            if restricted_until == 0:
                restricted_until = -1  # Restricted by bot, keep entry.restricted_until unchanged later
        else:
            restricted_until = 0

    with t_lock:
        ac_list, rec = record_empty_test('ac', list)
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id == whitelist_id and entry.whitelist_reason:
                entry.whitelist_reason = ''
                if restricted_until != -1:
                    entry.restricted_until = restricted_until
                ac_list[i] = entry.to_dict()
                break
        else:
            bot.send_message(msg.chat.id, text=config['messages']['remove_whitelist_not_found'],
                             reply_to_message_id=msg.id)
            return

        rec['ac'] = ac_list
        json.dump(rec, open(config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    log(config['messages']['remove_whitelist_log'].format(remover=remover.name, tg_id=whitelist_id))
    bot.send_message(msg.chat.id, text=config['messages']['remove_whitelist_succ'].format(tg_id=whitelist_id),
                     reply_to_message_id=msg.id)

    if not entry.confirmed:
        try:
            bot.silence_chat_member(config['group'], whitelist_id)
        except catbot.InsufficientRightError:
            bot.send_message(msg.chat.id, text=config['messages']['insufficient_right'])
        except catbot.RestrictAdminError:
            pass
        except catbot.UserNotFoundError:
            pass


def whois_cri(msg: catbot.Message) -> bool:
    return command_detector('/whois', msg) and msg.chat.id == config['group']


def whois(msg: catbot.Message):
    user_input_token = msg.text.split()
    if msg.reply:
        whois_id = msg.reply_to_message.from_.id
    else:
        if len(user_input_token) == 1:
            bot.send_message(config['group'], text=config['messages']['whois_prompt'], reply_to_message_id=msg.id)
            return
        else:
            try:
                whois_id = int(user_input_token[1])
            except ValueError:
                bot.send_message(config['group'], text=config['messages']['whois_inverse'],
                                 reply_to_message_id=msg.id)
                return

    with t_lock:
        ac_list, rec = record_empty_test('ac', list)
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id == whois_id:
                break
        else:
            bot.send_message(config['group'], text=config['messages']['whois_not_found'], reply_to_message_id=msg.id)
            return

    try:
        whois_member = bot.get_chat_member(config['group'], whois_id)
        name = whois_member.name
    except catbot.UserNotFoundError:
        name = ''
    resp_text = f'{name} ({whois_id})\n'
    if entry.confirmed:
        resp_text += config['messages']['whois_has_wikimedia'].format(
            wp_id=entry.wikimedia_username, ctime=time.strftime('%Y-%m-%d %H:%M', time.gmtime(entry.confirmed_time))
        )
    else:
        resp_text += config['messages']['whois_no_wikimedia']
    if entry.whitelist_reason:
        resp_text += config['messages']['whois_whitelisted'].format(reason=entry.whitelist_reason)

    bot.send_message(config['group'], text=resp_text, reply_to_message_id=msg.id, parse_mode='HTML',
                     disable_web_page_preview=True)


def refuse_cri(msg: catbot.Message) -> bool:
    return command_detector('/refuse', msg)


def refuse(msg: catbot.Message):
    try:
        operator = bot.get_chat_member(config['group'], msg.from_.id)
    except catbot.UserNotFoundError:
        return
    if not (operator.status == 'creator' or operator.status == 'administrator'):
        return

    if msg.reply:
        refused_id = msg.reply_to_message.from_.id
    else:
        user_input_token = msg.text.split()
        if len(user_input_token) < 2:
            bot.send_message(msg.chat.id, text=config['messages']['general_prompt'], reply_to_message_id=msg.id)
            return
        try:
            refused_id = int(user_input_token[1])
        except ValueError:
            return

    try:
        refused_user = bot.get_chat_member(config['group'], refused_id)
    except catbot.UserNotFoundError:
        restricted_until = 0
    else:
        if refused_user.status == 'restricted':
            restricted_until = refused_user.until_date
            if restricted_until == 0:
                restricted_until = -1
        else:
            restricted_until = 0

    with t_lock:
        ac_list, rec = record_empty_test('ac', list)
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id == refused_id:
                refused_index = i
                break
        else:
            entry = Ac(refused_id)
            ac_list.append(entry)
            refused_index = -1

        if restricted_until != -1:
            entry.restricted_until = restricted_until
        entry.confirmed = False
        entry.confirming = False
        entry.refused = True
        ac_list[refused_index] = entry.to_dict()
        rec['ac'] = ac_list
        json.dump(rec, open(config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    log(config['messages']['refuse_log'].format(tg_id=refused_id, refuser=operator.name))

    if not entry.whitelist_reason:
        try:
            bot.silence_chat_member(config['group'], refused_id)
        except catbot.InsufficientRightError:
            pass
        except catbot.RestrictAdminError:
            pass
        except catbot.UserNotFoundError:
            pass


def accept_cri(msg: catbot.Message) -> bool:
    return command_detector('/accept', msg)


def accept(msg: catbot.Message):
    operator = bot.get_chat_member(config['group'], msg.from_.id)
    if not (operator.status == 'creator' or operator.status == 'administrator'):
        return

    if msg.reply:
        accepted_id = msg.reply_to_message.from_.id
    else:
        user_input_token = msg.text.split()
        if len(user_input_token) < 2:
            bot.send_message(msg.chat.id, text=config['messages']['general_prompt'], reply_to_message_id=msg.id)
            return
        try:
            accepted_id = int(user_input_token[1])
        except ValueError:
            return

    with t_lock:
        ac_list, rec = record_empty_test('ac', list)
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id == accepted_id:
                accepted_index = i
                break
        else:
            entry = Ac(accepted_id)
            ac_list.append(entry)
            accepted_index = -1
        entry.refused = False
        ac_list[accepted_index] = entry.to_dict()
        rec['ac'] = ac_list
        json.dump(rec, open(config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    log(config['messages']['accept_log'].format(tg_id=accepted_id, acceptor=operator.name))


if __name__ == '__main__':
    bot.add_msg_task(start_cri, start)
    bot.add_msg_task(policy_cri, policy)
    bot.add_msg_task(confirm_cri, confirm)
    bot.add_query_task(confirm_button_cri, confirm_button)
    bot.add_msg_task(new_member_cri, new_member)
    bot.add_msg_task(add_whitelist_cri, add_whitelist)
    bot.add_msg_task(remove_whitelist_cri, remove_whitelist)
    bot.add_msg_task(deconfirm_cri, deconfirm)
    bot.add_query_task(deconfirm_button_cri, deconfirm_button)
    bot.add_msg_task(whois_cri, whois)
    bot.add_msg_task(refuse_cri, refuse)
    bot.add_msg_task(accept_cri, accept)

    while True:
        try:
            bot.start()
        except KeyboardInterrupt:
            break
        except:
            pass
