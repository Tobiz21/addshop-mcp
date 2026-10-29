"""Create a Windows-user-bound encrypted owner session for MCP startup."""
import argparse
import getpass

from addshop import Addshop
from login_cli import owner_login
from session_store import save_session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--session-file', required=True)
    args = parser.parse_args()
    client = Addshop(args.base_url)
    try:
        email = input('Addshop owner email: ')
        password = getpass.getpass('Addshop password (never saved): ')
        try:
            owner_login(client, email, password)
        finally:
            password = None
        cookie = '; '.join(f'{item.name}={item.value}' for item in client.http.cookies.jar)
        result = save_session(args.session_file, client.base, cookie)
        print(f'Encrypted session saved: {result["path"]}')
        print('Password saved: no')
    finally:
        client.http.close()


if __name__ == '__main__':
    main()
