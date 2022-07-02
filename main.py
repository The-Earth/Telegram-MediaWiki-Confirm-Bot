import json
import threading
import time
from calendar import timegm

import catbot
import mwclient
from catbot.util import html_refer

from ac import Ac

config = json.load(open('config.json', 'r', encoding='utf-8'))
bot = catbot.Bot(config)
t_lock = threading.Lock()
site = mwclient.Site(config['main_site'], reqs=bot.proxy_kw)


def log(text):
    bot.send_message(config['log_channel'], text=text, parse_mode='HTML', disable_web_page_preview=True)


def silence_trial(entry: Ac, alert_chat=0):
    member = bot.get_chat_member(config['group'], entry.telegram_id)
    if member.status == 'kicked':
        return
    if not (entry.confirmed or entry.whitelist_reason):
        try:
            bot.silence_chat_member(config['group'], entry.telegram_id)
        except catbot.InsufficientRightError:
            if alert_chat:
                bot.send_message(alert_chat, text=config['messages']['insufficient_right'])
        except catbot.RestrictAdminError:
            pass
        except catbot.UserNotFoundError:
            pass


def lift_restriction_trial(entry: Ac, alert_chat=0):
    member = bot.get_chat_member(config['group'], entry.telegram_id)
    if member.status == 'kicked':
        return
    try:
        if entry.restricted_until <= time.time() + 35:
            bot.lift_restrictions(config['group'], entry.telegram_id)
        else:
            bot.silence_chat_member(config['group'], entry.telegram_id, until=entry.restricted_until)
            bot.send_message(alert_chat,
                             text=config['messages']['restore_silence'].format(tg_id=entry.telegram_id),
                             parse_mode='HTML')
    except catbot.RestrictAdminError:
        pass
    except catbot.InsufficientRightError:
        if alert_chat:
            bot.send_message(alert_chat, text=config['messages']['insufficient_right'])
    except catbot.UserNotFoundError:
        pass


def start_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/start', msg) and msg.chat.type == 'private'


def start(msg: catbot.Message):
    bot.send_message(msg.chat.id, text=config['messages']['start'])


def policy_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/policy', msg)


def policy(msg: catbot.Message):
    bot.send_message(msg.chat.id, text=config['messages']['policy'], parse_mode='HTML')


def confirm_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/confirm', msg) and msg.chat.type == 'private'


def confirm(msg: catbot.Message):
    user_input_token = msg.text.split()
    if len(user_input_token) == 1:
        bot.send_message(msg.chat.id, text=config['messages']['confirm_prompt'], parse_mode='HTML')
        return

    mw_username = '_'.join(user_input_token[1:])
    mw_username = mw_username[0].upper() + mw_username[1:]
    bot.send_message(msg.chat.id, text=config['messages']['confirm_checking'])
    global_user_info_query = site.api(**{
        "action": "query",
        "format": "json",
        "meta": "globaluserinfo",
        "utf8": 1,
        "formatversion": "2",
        "guiuser": mw_username,
        "guiprop": "merged"
    })

    if 'missing' in global_user_info_query['query']['globaluserinfo'].keys():
        bot.send_message(msg.chat.id, text=config['messages']['confirm_user_not_found'].format(
            name=mw_username))
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
        ac_list, rec = bot.secure_record_fetch('ac', list)

        entry_index = -1
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id == msg.from_.id:
                if entry.confirmed:
                    bot.send_message(msg.chat.id,
                                     text=config['messages']['confirm_already'].format(
                                         wp_name=entry.mw_username))
                    return
                elif entry.confirming:
                    bot.send_message(msg.chat.id, text=config['messages']['confirm_confirming'])
                    return
                elif entry.refused:
                    bot.send_message(msg.chat.id, text=config['messages']['confirm_ineligible'])
                    return
                else:
                    entry_index = i

            elif entry.mw_username == mw_username and (entry.confirmed or entry.confirming):
                bot.send_message(msg.chat.id, text=config['messages']['confirm_conflict'])
                return
        else:
            if entry_index == -1:
                entry = Ac(msg.from_.id)
                ac_list.append(entry.to_dict())
            entry = Ac.from_dict(ac_list[entry_index])
            entry.confirming = True
            entry.mw_username = mw_username
            ac_list[entry_index] = entry.to_dict()

        rec['ac'] = ac_list
        json.dump(rec, open(config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    h = hash(time.time())
    button = catbot.InlineKeyboardButton(config['messages']['confirm_button'], callback_data=f'confirm_{h}')
    keyboard = catbot.InlineKeyboard([[button]])
    bot.send_message(msg.chat.id, text=config['messages']['confirm_wait'].format(site=config['main_site'],
                                                                                 page=config['page_for_confirmation'],
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
        ac_list, rec = bot.secure_record_fetch('ac', list)
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id != query.from_.id:
                continue
            if entry.confirmed:
                bot.send_message(query.msg.chat.id, text=config['messages']['confirm_already'].format(
                    wp_name=entry.mw_username))
                return
            if entry.confirming:
                entry_index = i
                break
        else:
            bot.send_message(query.msg.chat.id, text=config['messages']['confirm_session_lost'])
            return

        try:
            revs = site.Pages[config['page_for_confirmation']].revisions()
            while True:
                rev = next(revs)
                if 0 <= timegm(rev['timestamp']) - query.msg.date <= 180:
                    if rev['user'].replace(' ', '_') != entry.mw_username:
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

    if entry.confirmed:
        bot.send_message(query.msg.chat.id, text=config['messages']['confirm_complete'])
        lift_restriction_trial(entry)
        log(config['messages']['confirm_log'].format(tg_id=entry.telegram_id, wp_id=entry.mw_username,
                                                     site=config['main_site']))
    else:
        bot.send_message(query.msg.chat.id, text=config['messages']['confirm_failed'])


def deconfirm_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/deconfirm', msg) and msg.chat.type == 'private'


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
        ac_list, rec = bot.secure_record_fetch('ac', list)
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

    log(config['messages']['deconfirm_log'].format(tg_id=entry.telegram_id, wp_id=entry.mw_username,
                                                   site=config['main_site']))
    bot.send_message(query.msg.chat.id, text=config['messages']['deconfirm_succ'])

    silence_trial(entry)


def new_member_cri(msg: catbot.ChatMemberUpdate) -> bool:
    if not msg.chat.id == config['group']:
        return False
    elif msg.new_chat_member.is_bot:
        return False
    elif msg.from_.id != msg.new_chat_member.id:
        return False
    elif msg.new_chat_member.status == 'member':
        if msg.old_chat_member.status == 'left':
            return True
        elif msg.old_chat_member.status == 'restricted' and not msg.old_chat_member.is_member:
            return True
        else:
            return False
    elif msg.new_chat_member.status == 'restricted' and msg.new_chat_member.is_member:
        if msg.old_chat_member.status == 'left':
            return True
        elif msg.old_chat_member.status == 'restricted' and not msg.old_chat_member.is_member:
            return True
        else:
            return False
    else:
        return False


def new_member(msg: catbot.ChatMemberUpdate):
    if msg.new_chat_member.status == 'restricted':
        restricted_until = msg.new_chat_member.until_date
        if restricted_until == 0:
            restricted_until = -1  # Restricted by bot, keep entry.restricted_until unchanged later
    elif msg.new_chat_member.status == 'creator' or \
            msg.new_chat_member.status == 'administrator' or \
            msg.new_chat_member.status == 'kicked':
        return
    else:
        restricted_until = 0

    try:
        bot.silence_chat_member(config['group'], msg.new_chat_member.id)
    except catbot.InsufficientRightError:
        bot.send_message(config['group'], text=config['messages']['insufficient_right'])
        return

    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if entry.telegram_id == msg.new_chat_member.id:
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

    if entry.confirmed or entry.whitelist_reason:
        lift_restriction_trial(entry, config['group'])
    else:
        with t_lock:
            last_id, rec = bot.secure_record_fetch('last_welcome', int)
            cur = bot.send_message(config['group'],
                                   text=config['messages']['new_member_hint'].format(
                                       tg_id=msg.new_chat_member.id,
                                       tg_name=html_refer(msg.new_chat_member.name)),
                                   parse_mode='HTML')
            rec['last_welcome'] = cur.id
            json.dump(rec, open(config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
            try:
                bot.delete_message(config['group'], last_id)
            except catbot.DeleteMessageError:
                pass


def add_whitelist_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/add_whitelist', msg)


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
            bot.send_message(msg.chat.id, text=config['messages']['general_prompt'], reply_to_message_id=msg.id)
            return
        try:
            whitelist_id = int(user_input_token[1])
        except ValueError:
            bot.send_message(msg.chat.id, text=config['messages']['telegram_id_error'], reply_to_message_id=msg.id)
            return
        if len(user_input_token) > 2:
            reason = ' '.join(user_input_token[2:])
        else:
            reason = 'whitelisted'

    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)
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

    log(config['messages']['add_whitelist_log'].format(adder=html_refer(adder.name), tg_id=whitelist_id, reason=reason))
    bot.send_message(msg.chat.id, text=config['messages']['add_whitelist_succ'].format(tg_id=whitelist_id),
                     reply_to_message_id=msg.id)

    lift_restriction_trial(entry, msg.chat.id)


def remove_whitelist_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/remove_whitelist', msg)


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
            bot.send_message(msg.chat.id, text=config['messages']['general_prompt'], reply_to_message_id=msg.id)
            return
        try:
            whitelist_id = int(user_input_token[1])
        except ValueError:
            bot.send_message(msg.chat.id, text=config['messages']['telegram_id_error'], reply_to_message_id=msg.id)
            return

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
        ac_list, rec = bot.secure_record_fetch('ac', list)
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

    log(config['messages']['remove_whitelist_log'].format(remover=html_refer(remover.name), tg_id=whitelist_id))
    bot.send_message(msg.chat.id, text=config['messages']['remove_whitelist_succ'].format(tg_id=whitelist_id),
                     reply_to_message_id=msg.id)

    silence_trial(entry, msg.chat.id)


def whois_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/whois', msg) and msg.chat.id == config['group']


def whois(msg: catbot.Message):
    user_input_token = msg.text.split()
    if msg.reply:
        whois_id = msg.reply_to_message.from_.id
        whois_wm_name = ''
    else:
        if len(user_input_token) == 1:
            bot.send_message(config['group'], text=config['messages']['whois_prompt'], reply_to_message_id=msg.id)
            return
        else:
            try:
                whois_id = int(' '.join(user_input_token[1:]))
                whois_wm_name = ''
            except ValueError:
                whois_id = 0
                whois_wm_name = '_'.join(user_input_token[1:])
                whois_wm_name = whois_wm_name[0].upper() + whois_wm_name[1:]

    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)
        for i in range(len(ac_list)):
            entry = Ac.from_dict(ac_list[i])
            if (entry.confirmed or entry.whitelist_reason) and (entry.telegram_id == whois_id or (
                    entry.mw_username == whois_wm_name and whois_wm_name != '')):
                break
        else:
            bot.send_message(config['group'], text=config['messages']['whois_not_found'], reply_to_message_id=msg.id)
            return

    try:
        whois_member = bot.get_chat_member(config['group'], entry.telegram_id)
        name = html_refer(whois_member.name)
    except catbot.UserNotFoundError:
        name = config['messages']['whois_tg_name_unavailable']
    resp_text = f'{name} ({entry.telegram_id})\n'

    if entry.confirmed:
        resp_text += config['messages']['whois_has_mw'].format(
            wp_id=entry.mw_username,
            ctime=time.strftime('%Y-%m-%d %H:%M', time.gmtime(entry.confirmed_time)),
            site=config['main_site']
        )
    else:
        resp_text += config['messages']['whois_no_mw']
    if entry.whitelist_reason:
        resp_text += config['messages']['whois_whitelisted'].format(reason=entry.whitelist_reason)

    bot.send_message(config['group'], text=resp_text, reply_to_message_id=msg.id, parse_mode='HTML',
                     disable_web_page_preview=True)


def refuse_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/refuse', msg)


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
            bot.send_message(msg.chat.id, text=config['messages']['telegram_id_error'], reply_to_message_id=msg.id)
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
        ac_list, rec = bot.secure_record_fetch('ac', list)
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

    log(config['messages']['refuse_log'].format(tg_id=refused_id, refuser=html_refer(operator.name)))

    silence_trial(entry)


def accept_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/accept', msg)


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
            bot.send_message(msg.chat.id, text=config['messages']['telegram_id_error'], reply_to_message_id=msg.id)
            return

    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)
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

    log(config['messages']['accept_log'].format(tg_id=accepted_id, acceptor=html_refer(operator.name)))


def block_unconfirmed_cri(msg: catbot.Message) -> bool:
    return msg.chat.id == config['group']


def block_unconfirmed(msg: catbot.Message):
    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)
        for i, item in enumerate(ac_list):
            entry = Ac.from_dict(item)
            if entry.telegram_id == msg.from_.id and entry.confirmed:
                return
            if entry.telegram_id == msg.from_.id and entry.whitelist_reason:
                return

    try:
        bot.delete_message(config['group'], msg.id)
    except catbot.DeleteMessageError:
        print(f'[Error] Delete message {msg.id} failed.')


if __name__ == '__main__':
    bot.add_msg_task(start_cri, start)
    bot.add_msg_task(policy_cri, policy)
    bot.add_msg_task(confirm_cri, confirm)
    bot.add_query_task(confirm_button_cri, confirm_button)
    bot.add_member_status_task(new_member_cri, new_member)
    bot.add_msg_task(add_whitelist_cri, add_whitelist)
    bot.add_msg_task(remove_whitelist_cri, remove_whitelist)
    bot.add_msg_task(deconfirm_cri, deconfirm)
    bot.add_query_task(deconfirm_button_cri, deconfirm_button)
    bot.add_msg_task(whois_cri, whois)
    bot.add_msg_task(refuse_cri, refuse)
    bot.add_msg_task(accept_cri, accept)
    bot.add_msg_task(block_unconfirmed_cri, block_unconfirmed)

    while True:
        try:
            bot.start()
        except KeyboardInterrupt:
            break
        except:
            pass
