import json
import threading
import time
from calendar import timegm
from typing import Union
import re

import catbot
import mwclient
from catbot.util import html_escape
import requests

from acrecord import AcRecord

bot = catbot.Bot(config_path='config.json')
t_lock = threading.Lock()
site = mwclient.Site(bot.config['main_site'], reqs=bot.proxy_kw)


def log(text):
    bot.send_message(bot.config['log_channel'], text=text, parse_mode='HTML', disable_web_page_preview=True)


def silence_trial(ac_record: AcRecord, alert_chat=0):
    member = bot.get_chat_member(bot.config['group'], ac_record.telegram_id)
    if member.status == 'kicked':
        return
    if not (ac_record.confirmed or ac_record.whitelist_reason):
        try:
            bot.silence_chat_member(bot.config['group'], ac_record.telegram_id)
        except catbot.InsufficientRightError:
            if alert_chat:
                bot.send_message(alert_chat, text=bot.config['messages']['insufficient_right'])
        except catbot.RestrictAdminError:
            pass
        except catbot.UserNotFoundError:
            pass


def lift_restriction_trial(ac_record: AcRecord, alert_chat=0):
    member = bot.get_chat_member(bot.config['group'], ac_record.telegram_id)
    if member.status == 'kicked':
        return
    try:
        if ac_record.restricted_until <= time.time() + 35:
            bot.lift_restrictions(bot.config['group'], ac_record.telegram_id)
        else:
            bot.silence_chat_member(bot.config['group'], ac_record.telegram_id, until=ac_record.restricted_until)
            bot.send_message(alert_chat,
                             text=bot.config['messages']['restore_silence'].format(tg_id=ac_record.telegram_id),
                             parse_mode='HTML')
    except catbot.RestrictAdminError:
        pass
    except catbot.InsufficientRightError:
        if alert_chat:
            bot.send_message(alert_chat, text=bot.config['messages']['insufficient_right'])
    except catbot.UserNotFoundError:
        pass


def check_eligibility(query: catbot.CallbackQuery, mw_id: int) -> bool:
    bot.send_message(query.msg.chat.id, text=bot.config['messages']['confirm_checking'])
    global_user_info_query = site.api(**{
        "action": "query",
        "format": "json",
        "meta": "globaluserinfo",
        "utf8": 1,
        "formatversion": "2",
        "guiid": mw_id,
        "guiprop": "merged"
    })

    if 'error' in global_user_info_query.keys():
        bot.send_message(query.msg.chat.id, text=bot.config['messages']['confirm_user_not_found'].format(
            mw_id=mw_id))
        return False

    global_user_info = global_user_info_query['query']['globaluserinfo']['merged']
    for local_user in global_user_info:
        if local_user['editcount'] >= 50 and time.time() - \
                timegm(time.strptime(local_user['registration'], '%Y-%m-%dT%H:%M:%SZ')) > 7 * 86400:
            return True
    else:
        bot.send_message(query.msg.chat.id, text=bot.config['messages']['confirm_ineligible'])
        return False


def get_mw_username(mw_id: int) -> Union[str, None]:
    global_user_info_query = site.api(**{
        "action": "query",
        "format": "json",
        "meta": "globaluserinfo",
        "utf8": 1,
        "formatversion": "2",
        "guiid": mw_id,
    })

    if 'error' in global_user_info_query.keys():
        return None

    return global_user_info_query['query']['globaluserinfo']['name']


def get_mw_id(mw_username: str) -> Union[int, None]:
    global_user_info_query = site.api(**{
        "action": "query",
        "format": "json",
        "meta": "globaluserinfo",
        "utf8": 1,
        "formatversion": "2",
        "guiuser": mw_username,
    })

    if 'missing' in global_user_info_query['query']['globaluserinfo'].keys():
        return None

    return global_user_info_query['query']['globaluserinfo']['id']


def match_blacklist(token: str) -> bool:
    for reg in bot.config['blacklist']:
        if re.search(reg, token):
            return True

    return False


def start_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/start', msg) and msg.chat.type == 'private'


@bot.msg_task(start_cri)
def start(msg: catbot.Message):
    bot.send_message(msg.chat.id, text=bot.config['messages']['start'])


def policy_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/policy', msg)


@bot.msg_task(policy_cri)
def policy(msg: catbot.Message):
    bot.send_message(msg.chat.id, text=bot.config['messages']['policy'], parse_mode='HTML')


def confirm_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/confirm', msg) and msg.chat.type == 'private'


@bot.msg_task(confirm_cri)
def confirm(msg: catbot.Message):
    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)

        ac_record_index = -1
        for i in range(len(ac_list)):
            ac_record = ac_record.from_dict(ac_list[i])
            if ac_record.telegram_id == msg.from_.id:
                if ac_record.confirmed:
                    bot.send_message(msg.chat.id, text=bot.config['messages']['confirm_already'].format(
                        wp_name=get_mw_username(ac_record.mw_id)
                    ))
                    return
                elif ac_record.confirming:
                    bot.send_message(msg.chat.id, text=bot.config['messages']['confirm_confirming'])
                    return
                elif ac_record.refused:
                    bot.send_message(msg.chat.id, text=bot.config['messages']['confirm_ineligible'])
                    return
                else:
                    ac_record_index = i

        else:
            if ac_record_index == -1:
                ac_record = ac_record(msg.from_.id)
                ac_list.append(ac_record.to_dict())
            ac_record = ac_record.from_dict(ac_list[ac_record_index])
            ac_record.confirming = True
            ac_list[ac_record_index] = ac_record.to_dict()

        rec['ac'] = ac_list
        json.dump(rec, open(bot.config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    button = catbot.InlineKeyboardButton(bot.config['messages']['confirm_button'], callback_data=f'confirm')
    keyboard = catbot.InlineKeyboard([[button]])
    bot.send_message(
        msg.chat.id,
        text=bot.config['messages']['confirm_wait'].format(
            link=bot.config['oauth_auth_url'].format(
                telegram_id=msg.from_.id
            )
        ),
        parse_mode='HTML',
        disable_web_page_preview=True,
        reply_markup=keyboard
    )


def confirm_button_cri(query: catbot.CallbackQuery) -> bool:
    return query.data.startswith('confirm') and query.msg.chat.type == 'private'


@bot.query_task(confirm_button_cri)
def confirm_button(query: catbot.CallbackQuery):
    bot.answer_callback_query(callback_query_id=query.id)
    bot.edit_message(query.msg.chat.id, query.msg.id, text=query.msg.html_formatted_text, parse_mode='HTML',
                     disable_web_page_preview=True)
    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)
        for i in range(len(ac_list)):
            ac_record = ac_record.from_dict(ac_list[i])
            if ac_record.telegram_id != query.from_.id:
                continue
            if ac_record.confirmed:
                bot.send_message(query.msg.chat.id, text=bot.config['messages']['confirm_already'].format(
                    wp_name=get_mw_username(ac_record.mw_id)
                ))
                return
            if ac_record.confirming:
                ac_record_index = i
                break
        else:
            bot.send_message(query.msg.chat.id, text=bot.config['messages']['confirm_session_lost'])
            return

        try:
            res = requests.post(
                bot.config['oauth_query_url'],
                json={
                    'query_key': bot.config['oauth_query_key'],
                    'telegram_id': str(query.from_.id)
                }
            )
        except (requests.ConnectTimeout, requests.ConnectionError, requests.HTTPError):
            ac_record.confirmed = False
        else:
            if res.status_code == 200 and res.json()['ok']:
                ac_record.mw_id = res.json()['mw_id']
                ac_record.confirmed = check_eligibility(query, ac_record.mw_id)
            else:
                ac_record.confirmed = False
        finally:
            if ac_record.confirmed:
                ac_record.confirmed_time = time.time()
            ac_record.confirming = False

        ac_list[ac_record_index] = ac_record.to_dict()
        rec['ac'] = ac_list
        json.dump(rec, open(bot.config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    if ac_record.confirmed:
        bot.send_message(query.msg.chat.id, text=bot.config['messages']['confirm_complete'])
        lift_restriction_trial(ac_record)
        log(bot.config['messages']['confirm_log'].format(
            tg_id=ac_record.telegram_id,
            wp_name=get_mw_username(ac_record.mw_id),
            site=bot.config['main_site']
        ))
    else:
        bot.send_message(query.msg.chat.id, text=bot.config['messages']['confirm_failed'])


def deconfirm_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/deconfirm', msg) and msg.chat.type == 'private'


@bot.msg_task(deconfirm_cri)
def deconfirm(msg: catbot.Message):
    button = catbot.InlineKeyboardButton(bot.config['messages']['deconfirm_button'], callback_data='deconfirm')
    keyboard = catbot.InlineKeyboard([[button]])
    bot.send_message(msg.chat.id, text=bot.config['messages']['deconfirm_prompt'], reply_markup=keyboard)


def deconfirm_button_cri(query: catbot.CallbackQuery) -> bool:
    return query.data == 'deconfirm' and query.msg.chat.type == 'private'


@bot.query_task(deconfirm_button_cri)
def deconfirm_button(query: catbot.CallbackQuery):
    bot.answer_callback_query(query.id)
    try:
        user_chat = bot.get_chat_member(bot.config['group'], query.from_.id)
    except catbot.UserNotFoundError:
        restricted_until = 0
    else:
        if user_chat.status == 'restricted':
            restricted_until = user_chat.until_date
            if restricted_until == 0:
                restricted_until = -1  # Restricted by bot, keep ac_record.restricted_until unchanged later
        else:
            restricted_until = 0

    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)
        for i in range(len(ac_list)):
            ac_record = ac_record.from_dict(ac_list[i])
            if ac_record.telegram_id == query.from_.id:
                if ac_record.confirmed:
                    ac_record.confirmed = False
                    ac_list[i] = ac_record.to_dict()
                    if restricted_until != -1:
                        ac_record.restricted_until = restricted_until
                    break
                else:
                    bot.send_message(query.msg.chat.id, text=bot.config['messages']['deconfirm_not_confirmed'])
                    return
        else:
            bot.send_message(query.msg.chat.id, text=bot.config['messages']['deconfirm_not_confirmed'])
            return

        rec['ac'] = ac_list
        json.dump(rec, open(bot.config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    log(bot.config['messages']['deconfirm_log'].format(
        tg_id=ac_record.telegram_id,
        wp_name=get_mw_username(ac_record.mw_id),
        site=bot.config['main_site'])
    )
    bot.send_message(query.msg.chat.id, text=bot.config['messages']['deconfirm_succ'])

    silence_trial(ac_record)


def new_member_cri(msg: catbot.ChatMemberUpdate) -> bool:
    if not msg.chat.id == bot.config['group']:
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


@bot.member_status_task(new_member_cri)
def new_member(msg: catbot.ChatMemberUpdate):
    if msg.new_chat_member.status == 'restricted':
        restricted_until = msg.new_chat_member.until_date
        if restricted_until == 0:
            restricted_until = -1  # Restricted by bot, keep ac_record.restricted_until unchanged later
    elif msg.new_chat_member.status == 'creator' or \
            msg.new_chat_member.status == 'administrator' or \
            msg.new_chat_member.status == 'kicked':
        return
    else:
        restricted_until = 0

    try:
        bot.silence_chat_member(bot.config['group'], msg.new_chat_member.id)
        if match_blacklist(msg.new_chat_member.name):
            bot.kick_chat_member(bot.config['group'], msg.new_chat_member.id)
            return
    except catbot.InsufficientRightError:
        bot.send_message(bot.config['group'], text=bot.config['messages']['insufficient_right'])
        return

    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)
        for i in range(len(ac_list)):
            ac_record = ac_record.from_dict(ac_list[i])
            if ac_record.telegram_id == msg.new_chat_member.id:
                user_index = i
                break
        else:
            ac_record = ac_record(msg.from_.id)
            ac_list.append(ac_record)
            user_index = -1

        if restricted_until != -1:
            ac_record.restricted_until = restricted_until
        ac_list[user_index] = ac_record.to_dict()
        rec['ac'] = ac_list
        json.dump(rec, open(bot.config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    if ac_record.confirmed or ac_record.whitelist_reason:
        lift_restriction_trial(ac_record, bot.config['group'])
    else:
        with t_lock:
            last_id, rec = bot.secure_record_fetch('last_welcome', int)
            cur = bot.send_message(bot.config['group'],
                                   text=bot.config['messages']['new_member_hint'].format(
                                       tg_id=msg.new_chat_member.id,
                                       tg_name=html_escape(msg.new_chat_member.name)),
                                   parse_mode='HTML')
            rec['last_welcome'] = cur.id
            json.dump(rec, open(bot.config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
            try:
                bot.delete_message(bot.config['group'], last_id)
            except catbot.DeleteMessageError:
                pass


def add_whitelist_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/add_whitelist', msg)


@bot.msg_task(add_whitelist_cri)
def add_whitelist(msg: catbot.Message):
    adder = bot.get_chat_member(bot.config['group'], msg.from_.id)
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
            bot.send_message(
                msg.chat.id,
                text=bot.config['messages']['add_whitelist_prompt'],
                reply_to_message_id=msg.id
            )
            return
        try:
            whitelist_id = int(user_input_token[1])
        except ValueError:
            bot.send_message(msg.chat.id, text=bot.config['messages']['telegram_id_error'], reply_to_message_id=msg.id)
            return
        if len(user_input_token) > 2:
            reason = ' '.join(user_input_token[2:])
        else:
            reason = 'whitelisted'

    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)
        for i in range(len(ac_list)):
            ac_record = ac_record.from_dict(ac_list[i])
            if ac_record.telegram_id == whitelist_id:
                ac_record.whitelist_reason = reason
                ac_list[i] = ac_record.to_dict()
                break
        else:
            ac_record = ac_record(whitelist_id)
            ac_record.whitelist_reason = reason
            ac_list.append(ac_record.to_dict())

        rec['ac'] = ac_list
        json.dump(rec, open(bot.config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    log(bot.config['messages']['add_whitelist_log'].format(
        adder=html_escape(adder.name),
        tg_id=whitelist_id,
        reason=reason
    ))
    bot.send_message(msg.chat.id, text=bot.config['messages']['add_whitelist_succ'].format(tg_id=whitelist_id),
                     reply_to_message_id=msg.id)

    lift_restriction_trial(ac_record, msg.chat.id)


def remove_whitelist_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/remove_whitelist', msg)


@bot.msg_task(remove_whitelist_cri)
def remove_whitelist(msg: catbot.Message):
    try:
        remover = bot.get_chat_member(bot.config['group'], msg.from_.id)
    except catbot.UserNotFoundError:
        return
    if not (remover.status == 'creator' or remover.status == 'administrator'):
        return

    user_input_token = msg.text.split()
    if msg.reply:
        whitelist_id = msg.reply_to_message.from_.id
    else:
        if len(user_input_token) < 2:
            bot.send_message(msg.chat.id, text=bot.config['messages']['general_prompt'], reply_to_message_id=msg.id)
            return
        try:
            whitelist_id = int(user_input_token[1])
        except ValueError:
            bot.send_message(msg.chat.id, text=bot.config['messages']['telegram_id_error'], reply_to_message_id=msg.id)
            return

    try:
        whitelist_user = bot.get_chat_member(bot.config['group'], whitelist_id)
    except catbot.UserNotFoundError:
        restricted_until = 0
    else:
        if whitelist_user.status == 'restricted':
            restricted_until = whitelist_user.until_date
            if restricted_until == 0:
                restricted_until = -1  # Restricted by bot, keep ac_record.restricted_until unchanged later
        else:
            restricted_until = 0

    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)
        for i in range(len(ac_list)):
            ac_record = ac_record.from_dict(ac_list[i])
            if ac_record.telegram_id == whitelist_id and ac_record.whitelist_reason:
                ac_record.whitelist_reason = ''
                if restricted_until != -1:
                    ac_record.restricted_until = restricted_until
                ac_list[i] = ac_record.to_dict()
                break
        else:
            bot.send_message(msg.chat.id, text=bot.config['messages']['remove_whitelist_not_found'],
                             reply_to_message_id=msg.id)
            return

        rec['ac'] = ac_list
        json.dump(rec, open(bot.config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    log(bot.config['messages']['remove_whitelist_log'].format(remover=html_escape(remover.name), tg_id=whitelist_id))
    bot.send_message(msg.chat.id, text=bot.config['messages']['remove_whitelist_succ'].format(tg_id=whitelist_id),
                     reply_to_message_id=msg.id)

    silence_trial(ac_record, msg.chat.id)


def whois_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/whois', msg) and msg.chat.id == bot.config['group']


@bot.msg_task(whois_cri)
def whois(msg: catbot.Message):
    user_input_token = msg.text.split()
    if msg.reply:
        whois_id = msg.reply_to_message.from_.id
        whois_mw_id = None
    else:
        if len(user_input_token) == 1:
            bot.send_message(
                bot.config['group'],
                text=bot.config['messages']['whois_prompt'],
                reply_to_message_id=msg.id
            )
            return

        try:
            whois_id = int(' '.join(user_input_token[1:]))
            whois_mw_id = None
        except ValueError:
            whois_id = 0
            whois_wm_name = '_'.join(user_input_token[1:])
            whois_wm_name = whois_wm_name[0].upper() + whois_wm_name[1:]
            whois_mw_id = get_mw_id(whois_wm_name)
            if whois_mw_id is None:
                bot.send_message(bot.config['group'], text=bot.config['messages']['whois_not_found'],
                                 reply_to_message_id=msg.id)
                return

    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)
        for i in range(len(ac_list)):
            ac_record = ac_record.from_dict(ac_list[i])
            if (ac_record.confirmed or ac_record.whitelist_reason) and (ac_record.telegram_id == whois_id or
                                                                        ac_record.mw_id == whois_mw_id):
                break
        else:
            bot.send_message(
                bot.config['group'],
                text=bot.config['messages']['whois_not_found'],
                reply_to_message_id=msg.id
            )
            return

    try:
        whois_member = bot.get_chat_member(bot.config['group'], ac_record.telegram_id)
        name = html_escape(whois_member.name)
    except catbot.UserNotFoundError:
        name = bot.config['messages']['whois_tg_name_unavailable']
    resp_text = bot.config['messages']['whois_head'].format(
        name=name,
        tg_id=ac_record.telegram_id
    )

    if ac_record.confirmed:
        wp_username = get_mw_username(ac_record.mw_id)
        if wp_username is None:
            bot.send_message(
                bot.config['group'],
                text=bot.config['messages']['whois_not_found'],
                reply_to_message_id=msg.id
            )
            return
        resp_text += bot.config['messages']['whois_has_mw'].format(
            wp_id=html_escape(wp_username),
            ctime=time.strftime('%Y-%m-%d %H:%M', time.gmtime(ac_record.confirmed_time)),
            site=bot.config['main_site']
        )
    else:
        resp_text += bot.config['messages']['whois_no_mw']
    if ac_record.whitelist_reason:
        resp_text += bot.config['messages']['whois_whitelisted'].format(reason=ac_record.whitelist_reason)

    bot.send_message(bot.config['group'], text=resp_text, reply_to_message_id=msg.id, parse_mode='HTML',
                     disable_web_page_preview=True)


def refuse_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/refuse', msg)


@bot.msg_task(refuse_cri)
def refuse(msg: catbot.Message):
    try:
        operator = bot.get_chat_member(bot.config['group'], msg.from_.id)
    except catbot.UserNotFoundError:
        return
    if not (operator.status == 'creator' or operator.status == 'administrator'):
        return

    if msg.reply:
        refused_id = msg.reply_to_message.from_.id
    else:
        user_input_token = msg.text.split()
        if len(user_input_token) < 2:
            bot.send_message(msg.chat.id, text=bot.config['messages']['general_prompt'], reply_to_message_id=msg.id)
            return
        try:
            refused_id = int(user_input_token[1])
        except ValueError:
            bot.send_message(msg.chat.id, text=bot.config['messages']['telegram_id_error'], reply_to_message_id=msg.id)
            return

    try:
        refused_user = bot.get_chat_member(bot.config['group'], refused_id)
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
            ac_record = ac_record.from_dict(ac_list[i])
            if ac_record.telegram_id == refused_id:
                refused_index = i
                break
        else:
            ac_record = ac_record(refused_id)
            ac_list.append(ac_record)
            refused_index = -1

        if restricted_until != -1:
            ac_record.restricted_until = restricted_until
        ac_record.confirmed = False
        ac_record.confirming = False
        ac_record.refused = True
        ac_list[refused_index] = ac_record.to_dict()
        rec['ac'] = ac_list
        json.dump(rec, open(bot.config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    log(bot.config['messages']['refuse_log'].format(tg_id=refused_id, refuser=html_escape(operator.name)))

    silence_trial(ac_record)


def accept_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/accept', msg)


@bot.msg_task(accept_cri)
def accept(msg: catbot.Message):
    operator = bot.get_chat_member(bot.config['group'], msg.from_.id)
    if not (operator.status == 'creator' or operator.status == 'administrator'):
        return

    if msg.reply:
        accepted_id = msg.reply_to_message.from_.id
    else:
        user_input_token = msg.text.split()
        if len(user_input_token) < 2:
            bot.send_message(msg.chat.id, text=bot.config['messages']['general_prompt'], reply_to_message_id=msg.id)
            return
        try:
            accepted_id = int(user_input_token[1])
        except ValueError:
            bot.send_message(msg.chat.id, text=bot.config['messages']['telegram_id_error'], reply_to_message_id=msg.id)
            return

    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)
        for i in range(len(ac_list)):
            ac_record = ac_record.from_dict(ac_list[i])
            if ac_record.telegram_id == accepted_id:
                accepted_index = i
                break
        else:
            ac_record = ac_record(accepted_id)
            ac_list.append(ac_record)
            accepted_index = -1
        ac_record.refused = False
        ac_list[accepted_index] = ac_record.to_dict()
        rec['ac'] = ac_list
        json.dump(rec, open(bot.config['record'], 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    log(bot.config['messages']['accept_log'].format(tg_id=accepted_id, acceptor=html_escape(operator.name)))


def block_unconfirmed_cri(msg: catbot.Message) -> bool:
    return msg.chat.id == bot.config['group']


# @bot.msg_task(block_unconfirmed_cri)
def block_unconfirmed(msg: catbot.Message):
    if hasattr(msg, 'new_chat_members') or hasattr(msg, 'left_chat_member'):
        return
    with t_lock:
        ac_list, rec = bot.secure_record_fetch('ac', list)
        for i, item in enumerate(ac_list):
            ac_record = ac_record.from_dict(item)
            if ac_record.telegram_id == msg.from_.id and ac_record.confirmed:
                return
            if ac_record.telegram_id == msg.from_.id and ac_record.whitelist_reason:
                return

    try:
        bot.delete_message(bot.config['group'], msg.id)
    except catbot.DeleteMessageError:
        print(f'[Error] Delete message {msg.id} failed.')


if __name__ == '__main__':
    with bot:
        bot.start()
