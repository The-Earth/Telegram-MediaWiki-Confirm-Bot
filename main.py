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


class AcBot(catbot.Bot):
    def __init__(self, config_path='config.json'):
        super(AcBot, self).__init__(config_path=config_path)
        if 'ac' in self.record:
            self.ac_record: list[AcRecord] = [AcRecord.from_dict(x) for x in self.record['ac']]
        else:
            self.ac_record: list[AcRecord] = []

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.record['ac'] = [x.to_dict() for x in self.ac_record]
        super().__exit__(exc_type, exc_val, exc_tb)


bot = AcBot(config_path='config.json')
t_lock = threading.Lock()
site = mwclient.Site(bot.config['main_site'], connection_options={'proxies': bot.proxies})


def log(text):
    bot.send_message(bot.config['log_channel'], text=text, parse_mode='HTML', disable_web_page_preview=True)


def silence_trial(ac_record: AcRecord, chat_id: int, alert=False):
    member = bot.get_chat_member(chat_id, ac_record.telegram_id)
    if member.status == 'kicked':
        return
    if not (ac_record.confirmed or ac_record.whitelist_reason[chat_id]):
        try:
            bot.silence_chat_member(chat_id, ac_record.telegram_id)
            if alert:
                bot.send_message(chat_id, text=bot.config['messages']['silence_alert'].format(
                    name=member.name,
                    tg_id=ac_record.telegram_id,
                ), parse_mode='HTML')
        except catbot.InsufficientRightError:
            if alert:
                bot.send_message(chat_id, text=bot.config['messages']['insufficient_right'])
        except catbot.RestrictAdminError:
            pass
        except catbot.UserNotFoundError:
            pass


def lift_restriction_trial(ac_record: AcRecord, chat_id: int, alert=False):
    member = bot.get_chat_member(chat_id, ac_record.telegram_id)
    if member.status == 'kicked':
        return
    try:
        bot.lift_restrictions(chat_id, ac_record.telegram_id)
        if alert:
            bot.send_message(chat_id, text=bot.config['messages']['lift_restriction_alert'].format(
                name=member.name,
                tg_id=ac_record.telegram_id
            ), parse_mode='HTML')
    except catbot.RestrictAdminError:
        pass
    except catbot.InsufficientRightError:
        if alert:
            bot.send_message(chat_id, text=bot.config['messages']['insufficient_right'])
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
    bot.send_message(msg.chat.id, text=bot.config['messages']['start'], parse_mode='HTML')


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
        records_of_id = list(filter(lambda x: x.telegram_id == msg.from_.id, bot.ac_record))
        if len(records_of_id) == 0:
            ac_record = AcRecord(msg.from_.id)
            bot.ac_record.append(ac_record)
            ac_record.confirming = True
        else:
            ac_record: AcRecord = records_of_id[0]
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
                ac_record.confirming = True

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
        records_of_id = list(filter(lambda x: x.telegram_id == query.from_.id, bot.ac_record))
        if len(records_of_id) == 0:
            bot.send_message(query.msg.chat.id, text=bot.config['messages']['confirm_session_lost'])
            return
        else:
            ac_record: AcRecord = records_of_id[0]
            if ac_record.confirmed:
                bot.send_message(query.msg.chat.id, text=bot.config['messages']['confirm_already'].format(
                    wp_name=get_mw_username(ac_record.mw_id)
                ))
                return
            elif not ac_record.confirming:
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
                mw_id: int = res.json()['mw_id']
                ac_record.mw_id = mw_id

                same_mw_id_record = list(filter(lambda x: x.mw_id == mw_id and x.confirmed, bot.ac_record))
                if len(same_mw_id_record) >= 1:
                    bot.send_message(
                        query.msg.chat.id,
                        text=bot.config['messages']['confirm_other_tg'].format(wp_name=get_mw_username(mw_id))
                    )
                else:
                    ac_record.confirmed = check_eligibility(query, ac_record.mw_id)
            else:
                ac_record.confirmed = False
        finally:
            if ac_record.confirmed:
                ac_record.confirmed_time = time.time()
            ac_record.confirming = False

    if ac_record.confirmed:
        bot.send_message(query.msg.chat.id, text=bot.config['messages']['confirm_complete'])
        for chat_id in bot.config['groups']:
            lift_restriction_trial(ac_record, chat_id, alert=True)
        log(bot.config['messages']['confirm_log'].format(
            tg_id=ac_record.telegram_id,
            wp_name=get_mw_username(ac_record.mw_id),
            site=bot.config['main_site']
        ))
    else:
        bot.send_message(query.msg.chat.id, text=bot.config['messages']['confirm_failed'], parse_mode='HTML')


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

    with t_lock:
        records_of_id = list(filter(lambda x: x.telegram_id == query.from_.id, bot.ac_record))
        if len(records_of_id) == 0:
            bot.send_message(query.msg.chat.id, text=bot.config['messages']['deconfirm_not_confirmed'])
            return
        else:
            ac_record: AcRecord = records_of_id[0]
            if ac_record.telegram_id == query.from_.id:
                if ac_record.confirmed:
                    ac_record.confirmed = False
                else:
                    bot.send_message(query.msg.chat.id, text=bot.config['messages']['deconfirm_not_confirmed'])
                    return

    log(bot.config['messages']['deconfirm_log'].format(
        tg_id=ac_record.telegram_id,
        wp_name=get_mw_username(ac_record.mw_id),
        site=bot.config['main_site']
    ))
    bot.send_message(query.msg.chat.id, text=bot.config['messages']['deconfirm_succ'])

    for chat_id in bot.config['groups']:
        silence_trial(ac_record, chat_id, alert=True)


def new_member_cri(msg: catbot.ChatMemberUpdate) -> bool:
    if msg.chat.id not in bot.config['groups']:
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
        bot.silence_chat_member(msg.chat.id, msg.new_chat_member.id)
        if match_blacklist(msg.new_chat_member.name):
            bot.kick_chat_member(msg.chat.id, msg.new_chat_member.id)
            return
    except catbot.InsufficientRightError:
        bot.send_message(msg.chat.id, text=bot.config['messages']['insufficient_right'])
        return

    with t_lock:
        records_of_id = list(filter(lambda x: x.telegram_id == msg.from_.id, bot.ac_record))
        if len(records_of_id) == 0:
            ac_record = AcRecord(msg.from_.id)
            bot.ac_record.append(ac_record)
        else:
            ac_record: AcRecord = records_of_id[0]

        if restricted_until != -1:
            ac_record.restricted_until = restricted_until

    if ac_record.confirmed or ac_record.whitelist_reason[msg.chat.id]:
        lift_restriction_trial(ac_record, msg.chat.id, alert=True)
    else:
        with t_lock:
            cur = bot.send_message(
                msg.chat.id,
                text=bot.config['messages']['new_member_hint'].format(
                    tg_id=msg.new_chat_member.id,
                    tg_name=html_escape(msg.new_chat_member.name)
                ),
                parse_mode='HTML'
            )
            if 'last_welcome' in bot.record and str(msg.chat.id) in bot.record['last_welcome']:
                last_id = bot.record['last_welcome'][str(msg.chat.id)]
                try:
                    bot.delete_message(msg.chat.id, last_id)
                except catbot.DeleteMessageError:
                    pass
            if 'last_welcome' in bot.record:
                bot.record['last_welcome'][str(msg.chat.id)] = cur.id
            else:
                bot.record['last_welcome'] = {str(msg.chat.id): cur.id}


def add_whitelist_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/add_whitelist', msg) and msg.chat.id in bot.config['groups']


@bot.msg_task(add_whitelist_cri)
def add_whitelist(msg: catbot.Message):
    try:
        adder = bot.get_chat_member(msg.chat.id, msg.from_.id)
    except catbot.UserNotFoundError:
        return
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
        records_of_id = list(filter(lambda x: x.telegram_id == whitelist_id, bot.ac_record))
        if len(records_of_id) == 0:
            ac_record = AcRecord(whitelist_id)
            ac_record.whitelist_reason[msg.chat.id] = reason
            bot.ac_record.append(ac_record)
        else:
            ac_record: AcRecord = records_of_id[0]
            ac_record.whitelist_reason[msg.chat.id] = reason

    log(bot.config['messages']['add_whitelist_log'].format(
        adder=html_escape(adder.name),
        tg_id=whitelist_id,
        reason=reason
    ))
    bot.send_message(
        msg.chat.id,
        text=bot.config['messages']['add_whitelist_succ'].format(tg_id=whitelist_id),
        reply_to_message_id=msg.id,
        parse_mode='HTML'
    )

    lift_restriction_trial(ac_record, msg.chat.id)


def remove_whitelist_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/remove_whitelist', msg) and msg.chat.id in bot.config['groups']


@bot.msg_task(remove_whitelist_cri)
def remove_whitelist(msg: catbot.Message):
    try:
        remover = bot.get_chat_member(msg.chat.id, msg.from_.id)
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

    with t_lock:
        records_of_id = list(filter(lambda x: x.telegram_id == whitelist_id and x.whitelist_reason[msg.chat.id], bot.ac_record))
        if len(records_of_id) == 0:
            bot.send_message(
                msg.chat.id,
                text=bot.config['messages']['remove_whitelist_not_found'],
                reply_to_message_id=msg.id
            )
            return
        else:
            ac_record: AcRecord = records_of_id[0]
            ac_record.whitelist_reason[msg.chat.id] = ''

    log(bot.config['messages']['remove_whitelist_log'].format(remover=html_escape(remover.name), tg_id=whitelist_id))
    bot.send_message(
        msg.chat.id,
        text=bot.config['messages']['remove_whitelist_succ'].format(tg_id=whitelist_id),
        reply_to_message_id=msg.id
    )

    silence_trial(ac_record, msg.chat.id)


def whois_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/whois', msg) and msg.chat.id in bot.config['groups']


@bot.msg_task(whois_cri)
def whois(msg: catbot.Message):
    user_input_token = msg.text.split()
    if msg.reply:
        whois_id = msg.reply_to_message.from_.id
        whois_mw_id = None
    else:
        if len(user_input_token) == 1:
            bot.send_message(
                msg.chat.id,
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
                bot.send_message(
                    msg.chat.id,
                    text=bot.config['messages']['whois_not_found'],
                    reply_to_message_id=msg.id
                )
                return

    with t_lock:
        def match(x):
            return (x.confirmed or x.whitelist_reason[msg.chat.id]) and (x.telegram_id == whois_id or x.mw_id == whois_mw_id)
        records_of_id = list(filter(match, bot.ac_record))
        if len(records_of_id) == 0:
            bot.send_message(
                msg.chat.id,
                text=bot.config['messages']['whois_not_found'],
                reply_to_message_id=msg.id
            )
            return
        else:
            ac_record: AcRecord = records_of_id[0]

    try:
        whois_member = bot.get_chat_member(msg.chat.id, ac_record.telegram_id)
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
                msg.chat.id,
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
    if ac_record.whitelist_reason[msg.chat.id]:
        resp_text += bot.config['messages']['whois_whitelisted'].format(reason=ac_record.whitelist_reason[msg.chat.id])

    bot.send_message(
        msg.chat.id,
        text=resp_text,
        reply_to_message_id=msg.id,
        parse_mode='HTML',
        disable_web_page_preview=True
    )


def refuse_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/refuse', msg) and msg.chat.id in bot.config['groups']


@bot.msg_task(refuse_cri)
def refuse(msg: catbot.Message):
    try:
        operator = bot.get_chat_member(msg.chat.id, msg.from_.id)
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

    with t_lock:
        records_of_id = list(filter(lambda x: x.telegram_id == refused_id, bot.ac_record))
        if len(records_of_id) == 0:
            ac_record = AcRecord(refused_id)
            bot.ac_record.append(ac_record)
        else:
            ac_record: AcRecord = records_of_id[0]

        ac_record.confirmed = False
        ac_record.confirming = False
        ac_record.refused = True

    log(bot.config['messages']['refuse_log'].format(tg_id=refused_id, refuser=html_escape(operator.name)))

    for chat_id in bot.config['groups']:
        silence_trial(ac_record, chat_id)


def accept_cri(msg: catbot.Message) -> bool:
    return bot.detect_command('/accept', msg) and msg.chat.id in bot.config['groups']


@bot.msg_task(accept_cri)
def accept(msg: catbot.Message):
    operator = bot.get_chat_member(msg.chat.id, msg.from_.id)
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
        records_of_id = list(filter(lambda x: x.telegram_id == accepted_id, bot.ac_record))
        if len(records_of_id) == 0:
            ac_record = AcRecord(accepted_id)
            bot.ac_record.append(ac_record)
        else:
            ac_record = records_of_id[0]

        ac_record.refused = False

    log(bot.config['messages']['accept_log'].format(tg_id=accepted_id, acceptor=html_escape(operator.name)))


def block_unconfirmed_cri(msg: catbot.Message) -> bool:
    if msg.chat.id not in bot.config['groups']:
        return False
    elif msg.from_.is_bot:
        return False
    elif hasattr(msg, 'new_chat_members') or hasattr(msg, 'left_chat_member'):
        return False
    else:
        return True


# @bot.msg_task(block_unconfirmed_cri)
def block_unconfirmed(msg: catbot.Message):
    with t_lock:
        def match(x):
            return x.telegram_id == msg.from_.id and (x.confirmed or x.whitelist_reason[msg.chat.id])
        records_of_id = list(filter(match, bot.ac_record))
        if len(records_of_id) > 0:
            return

    try:
        bot.delete_message(msg.chat.id, msg.id)
    except catbot.DeleteMessageError:
        print(f'[Error] Delete message {msg.id} failed.')


def enable_cri(msg: catbot.Message):
    return bot.detect_command('/enable', msg, require_username=True) and msg.chat.type != 'private'


@bot.msg_task(enable_cri)
def enable(msg: catbot.Message):
    try:
        adder = bot.get_chat_member(msg.chat.id, msg.from_.id)
    except catbot.UserNotFoundError:
        return
    if not (adder.status == 'creator' or adder.status == 'administrator'):
        return

    with t_lock:
        new_groups = set(bot.config['groups'])
        new_groups.add(msg.chat.id)
        bot.config['groups'] = list(new_groups)

    bot.send_message(msg.chat.id, bot.config['messages']['enable'], reply_to_message_id=msg.id)
    chat = bot.get_chat(msg.chat.id)
    log(bot.config["messages"]["enable_log"].format(
        tg_id=adder.id,
        enabler=adder.name,
        chat_link=chat.invite_link,
        chat_name=chat.name
    ))


def disable_cri(msg: catbot.Message):
    return bot.detect_command('/disable', msg, require_username=True) and msg.chat.type != 'private'


@bot.msg_task(disable_cri)
def disable(msg: catbot.Message):
    try:
        adder = bot.get_chat_member(msg.chat.id, msg.from_.id)
    except catbot.UserNotFoundError:
        return
    if not (adder.status == 'creator' or adder.status == 'administrator'):
        return

    with t_lock:
        bot.config['groups'].remove(msg.chat.id)

    bot.send_message(msg.chat.id, bot.config['messages']['disable'], reply_to_message_id=msg.id)
    chat = bot.get_chat(msg.chat.id)
    log(bot.config["messages"]["disable_log"].format(
        tg_id=adder.id,
        disabler=adder.name,
        chat_link=chat.invite_link,
        chat_name=chat.name
    ))


if __name__ == '__main__':
    with bot:
        bot.start()
