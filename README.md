# Telegram-MediaWiki-Confirm-Bot

验证并关联 Mediawiki 站点账户与 Telegram 账户，并在 Telegram 群组中禁止未通过验证的人发言。适用于安装了[CentralAuth](https://www.mediawiki.org/wiki/Extension:CentralAuth)和[OAuth](https://www.mediawiki.org/wiki/Extension:OAuth)的mediawiki网站。两者均未安装的网站可使用`single_wiki`分支的版本，安装了前者但未安装后者的版本可使用`non_oauth`分支。以下说明以维基百科为例。

## 常见问题

- 机器人何时会尝试禁言？
    - 用户入群、自行解除与站内账户的关联、被群管移出白名单时。不过每次尝试时，都会先检查用户是否有其他因素允许其发言。例如用户自行完成验证，此时即使移出白名单也不会被禁言。
- 机器人何时会尝试解除禁言？
    - 用户完成验证、被群管加入白名单时。尝试解除禁言时，机器人会检查先前记录下来的禁言期限，并恢复该禁言，从而防止利用机器人解除禁言的机制绕开群管实施的禁言。
- 可以同时验证站内账户与加入白名单吗？
    - 可以。这是两个独立的状态，用户只要具有其中一个就可以发言。
- 启用机器人之前已经在群里的用户会被禁言吗？
    - 不会。不过当他们解除验证或被移出白名单时，仍然会被禁言。
- 机器人故障时入群的人，在机器人复工后会被如何处理？
    - 不会有任何处理。

## 使用说明

### 启用机器人

将机器人设置为管理员并赋予禁言和删除权限，然后发送  `/enable@机器人用户名`。

### 停用机器人

移除机器人的管理员权限，发送 `/disable@机器人用户名`，

### 查询用户信息

在群里用 `/whois` 回复要查询的用户，或者发送 `/whois 用户ID`，可查询用户相应的站内账户，或加入白名单的情况。

### 验证维基百科站内账户

私聊机器人，发送 `/confirm`。机器人会给您一个链接以完成[ OAuth 认证](https://www.mediawiki.org/wiki/Help:OAuth/zh)。然后机器人会检查您提供的用户名是否注册超过 7 日，并编辑 50 次以上，您需要在至少一个维基媒体计划中达到这个标准。

### 解除与维基百科账户的关联

私聊机器人，发送 `/deconfirm` 。机器人会提供给您一个按钮，按下按钮后直接解除关联，同时机器人会尝试在群组中禁言您。解除按钮没有时间限制。

### 将用户加入白名单

发送 `/add_whitelist 123456789 备注` ，把 `123456789` 替换成 Telegram 用户的 ID，备注可以为空。在群里也可以直接回复该用户，省略用户 ID。

### 将用户移出白名单

发送 `/remove_whitelist 123456789`，把 `123456789` 替换成 Telegram 用户的 ID。在群里也可以直接回复该用户，省略用户 ID。

### 禁止用户验证账户

发送 `/refuse 123456789`，把 `123456789` 替换成 Telegram 用户的 ID。在群里也可以直接回复该用户，省略用户 ID。用户将被取消验证验证状态，且无法自行重新验证。白名单状态不会改变。

### 允许用户验证账户

发送 `/accept 123456789`，把 `123456789` 替换成 Telegram 用户的 ID。在群里也可以直接回复该用户，省略用户 ID。用户将被允许自行验证站内账户，白名单状态不变。

## 操作者说明

- 如何启用机器人？
    - 用 `requirements.txt` 安装依赖模块
    - 在 [Toolforge](https://wikitech.wikimedia.org/wiki/Portal:Toolforge) 新建工具，运行[这个仓库](https://github.com/The-Earth/Telegram-MediaWiki-Confirm-Bot-OAuth)的代码。
    - 将 `config_example.json` 中的 `token` 换成您自己机器人的 token，`proxy` 按实际需要设置；修改 `group` 和 `log_channel` 为需要验证的群组和验证日志频道的 ID；按需要修改主站点域名 `main_site`（会影响一些链接）；修改 `oauth_query_key` 与[您的 OAuth](https://github.com/The-Earth/Telegram-MediaWiki-Confirm-Bot-OAuth) 上同名的配置相同。
    - 把修改好的 `config_example.json` 的内容保存到 `config.json`
    - 运行 `main.py`
- OAuth 的部分在哪里？
  - [这里](https://github.com/The-Earth/Telegram-MediaWiki-Confirm-Bot-OAuth)。这部分代码在 Toolforge 运行。
- 是否只能用于验证维基媒体计划？
    - 不是。但与之相关的 OAuth 需由上一个问题中提到的代码支持。`non_oauth`和`single_wiki`分支则无需额外代码。
- 是否可以仅检查指定的几个维基，而非全域的所有站点？
    - 仅修改 config 不可以。但只需小小地修改 `main.py` 中的一处即可达到这个目的，config 中也预留了这个配置。
