#!/usr/bin/python3
# -*- coding: utf-8 -*-

import json
import os
import subprocess
import traceback

from dsf.connections import CommandConnection, InterceptConnection
from dsf.commands.basecommands import LogLevel, MessageType
from dsf.commands.code import CodeType
from dsf.initmessages.clientinitmessages import InterceptionMode

from http_endpoints import custom_http_endpoint
from MCodeAction import MCodeAction


DEFAULT_FILTERS = ["M1200"]
PLUGIN_NAME = "ExecOnMcode"


def __get_action_for_code(actions, received_code):
    for action in actions:
        if action.cmd_code == received_code:
            return action


def __get_filters_from_actions(actions):
    filters = [a.cmd_code for a in actions]
    filters.extend(DEFAULT_FILTERS)
    return filters


def intercept_mcodes(actions):
    filters = __get_filters_from_actions(actions)
    # TODO: Set InterceptionMode and debug from settings
    intercept_connection = InterceptConnection(InterceptionMode.PRE, filters=filters, debug=True)
    intercept_connection.connect()

    try:
        while True:
            # Wait for a code to arrive
            code = intercept_connection.receive_code()

            # Check for the type of the code
            if code.type != CodeType.MCode:
                intercept_connection.ignore_code()
                continue

            if code.majorNumber == 1200:  # Update interception filters
                actions = get_actions_from_config()
                filters = __get_filters_from_actions(actions)
                msg = f"{PLUGIN_NAME}: Interception filters successfully updated"
                intercept_connection.resolve_code(MessageType.Success, msg)
                intercept_connection.close()
                # TODO: Set InterceptionMode and debug from settings
                intercept_connection = InterceptConnection(InterceptionMode.PRE, filters=filters, debug=True)
                intercept_connection.connect()
            elif code.short_str() in filters:  # Do actions from JSON file
                error_msg = ""
                out = None
                action = __get_action_for_code(actions, code.short_str())
                if not action or not action.cmd_enabled:
                    intercept_connection.ignore_code()
                    continue
                if action.cmd_flush:
                    # Flush the code's channel to be sure we are being in sync with the machine
                    success = intercept_connection.flush(code.channel)
                    if not success:
                        print("Flush failed")
                        intercept_connection.cancel_code()
                        continue
                # TODO: use user
                try:
                    out = subprocess.run(action.cmd_command,
                                         shell=True,
                                         timeout=action.cmd_timeout,
                                         capture_output=action.cmd_capture_output,
                                         text=True)
                except subprocess.TimeoutExpired as e:
                    error_msg = f"Timeout expired on `{e.cmd}`."
                    if action.capture_output and e.output:
                        error_msg += f"\nOutput was: {e.output}"

                # Resolve the received code and return result
                if error_msg:
                    intercept_connection.resolve_code(MessageType.Error, error_msg)
                elif action.cmd_capture_output and out:
                    intercept_connection.resolve_code(MessageType.Success, out.stdout)
                else:
                    intercept_connection.resolve_code()
            else:
                # We did not handle it so we ignore it and it will be continued to be processed
                intercept_connection.ignore_code()
    except Exception as e:
        print("Closing connection: ", e)
        traceback.print_exc()
        intercept_connection.close()


def write_message(msg, msgType=MessageType.Success, loglvl=LogLevel.Info):
    cmd_conn.write_message(msgType, f"{PLUGIN_NAME}: {msg}", True, loglvl)


def get_actions_from_config():
    actions = []
    # Use DSF API to get the physical path to the configuration file
    res = cmd_conn.resolve_path(f"0:/sys/{PLUGIN_NAME}.json")
    filter_filepath = res.result if res else None
    if not os.path.isfile(filter_filepath):
        if filter_filepath:
            # Create a blank default file as example
            default_file_data = [
                {
                    'cmd_code': 'M1201',
                    'cmd_name': 'Echo test',
                    'cmd_command': f"echo 'If you can see this, it means {PLUGIN_NAME} is working !'",
                    'cmd_user': '',
                    'cmd_timeout': 30,
                    'cmd_capture_output': False,
                    'cmd_flush': False,
                    'cmd_enabled': True
                }
            ]
            with open(filter_filepath, 'w') as fp:
                fp.write(json.dumps(default_file_data, indent=4))
        return actions

    with open(filter_filepath) as fp:
        json_filter = json.load(fp)
        for action in json_filter:
            if action['cmd_code'] in DEFAULT_FILTERS:
                write_message(
                    f"{action['cmd_code']} is a reserved filter and thus it can't be used.",
                    MessageType.Error,
                    LogLevel.Warn)
                continue
            try:
                actions.append(MCodeAction(action))
            except KeyError as e:
                write_message(
                    e,
                    MessageType.Error,
                    LogLevel.Warn)
    return actions


if __name__ == "__main__":
    cmd_conn = CommandConnection()
    try:
        cmd_conn.connect()
        endpoint = custom_http_endpoint(cmd_conn)
        intercept_mcodes(get_actions_from_config())
    finally:
        if endpoint:
            endpoint.close()
        cmd_conn.close()

