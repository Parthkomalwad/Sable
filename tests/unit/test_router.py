"""Unit tests for shell/router.py.

Tests the classify() function with 30+ inputs covering:
- Clear bash commands
- Clear NL inputs
- Edge cases (e.g. 'find large log files' first word is a binary but rest is English)
- Prefix mode ('>>' prefix)
- Ambiguous inputs

No LLM calls, no subprocess, no file I/O.
"""
import pytest
from shell.router import Route, classify


class TestPrefixMode:
    def test_prefix_routes_agentic(self):
        assert classify(">> list all running processes", mode="prefix") == Route.AGENTIC

    def test_no_prefix_routes_bash(self):
        assert classify("ls -la", mode="prefix") == Route.BASH

    def test_prefix_with_bash_looking_command(self):
        assert classify(">> ls -la /home", mode="prefix") == Route.AGENTIC


class TestAutoModeBasic:
    def test_ls_routes_bash(self):
        assert classify("ls -la") == Route.BASH

    def test_cd_routes_bash(self):
        assert classify("cd /var/www") == Route.BASH

    def test_grep_routes_bash(self):
        assert classify("grep -r error /var/log") == Route.BASH

    def test_cat_routes_bash(self):
        assert classify("cat /etc/hosts") == Route.BASH

    def test_git_routes_bash(self):
        assert classify("git status") == Route.BASH

    def test_docker_routes_bash(self):
        assert classify("docker ps -a") == Route.BASH

    def test_pipe_routes_bash(self):
        assert classify("ps aux | grep nginx") == Route.BASH

    def test_redirect_routes_bash(self):
        assert classify("echo hello > /tmp/test.txt") == Route.BASH

    def test_background_routes_bash(self):
        assert classify("sleep 60 &") == Route.BASH

    def test_double_ampersand_routes_bash(self):
        assert classify("cd /tmp && ls") == Route.BASH


class TestAutoModeNL:
    def test_what_question_routes_agentic(self):
        assert classify("what is using all my disk space") == Route.AGENTIC

    def test_how_question_routes_agentic(self):
        assert classify("how do I restart nginx") == Route.AGENTIC

    def test_show_me_routes_agentic(self):
        assert classify("show me all running docker containers") == Route.AGENTIC

    def test_list_all_routes_agentic(self):
        assert classify("list all users with sudo access") == Route.AGENTIC

    def test_find_all_routes_agentic(self):
        assert classify("find all log files over 100mb") == Route.AGENTIC

    def test_check_if_routes_agentic(self):
        assert classify("check if port 80 is open") == Route.AGENTIC

    def test_give_me_routes_agentic(self):
        assert classify("give me the top 5 memory-consuming processes") == Route.AGENTIC

    def test_can_you_routes_agentic(self):
        assert classify("can you set up a cron job to clean logs daily") == Route.AGENTIC

    def test_articles_route_agentic(self):
        assert classify("the nginx config is broken, what do I fix") == Route.AGENTIC

    def test_why_question_routes_agentic(self):
        assert classify("why is the server running slow") == Route.AGENTIC


class TestEdgeCases:
    def test_find_large_log_files_routes_agentic(self):
        # 'find' is a known binary, but rest is English prose should be agentic
        assert classify("find large log files") == Route.AGENTIC

    def test_find_with_flags_routes_bash(self):
        # 'find' with flags is clearly bash
        assert classify("find /var/log -name '*.log' -size +100M") == Route.BASH

    def test_kill_process_nl_routes_agentic(self):
        assert classify("kill the nginx process") == Route.AGENTIC

    def test_kill_with_signal_routes_bash(self):
        assert classify("kill -9 1234") == Route.BASH

    def test_empty_string(self):
        # empty input should not crash
        result = classify("")
        assert result in (Route.BASH, Route.AGENTIC, Route.AMBIGUOUS)

    def test_single_word_known_binary(self):
        assert classify("bash") == Route.BASH

    def test_help_me_routes_agentic(self):
        assert classify("help me configure ssh key auth") == Route.AGENTIC

    def test_plain_filename_routes_bash(self):
        assert classify("./run.sh") == Route.BASH

    def test_env_var_expansion_routes_bash(self):
        assert classify("echo $HOME") == Route.BASH

    def test_subshell_routes_bash(self):
        assert classify("$(date)") == Route.BASH
