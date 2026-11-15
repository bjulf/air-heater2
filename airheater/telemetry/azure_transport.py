"""Shared device-to-cloud transport; never log connection strings."""
import json
import os
from pathlib import Path


def create_transport(transport, device_id, websockets=False):
    if transport == 'stdout':
        async def send(body):
            print(body, flush=True)
        return None, send

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / '.env',
                override=False, interpolate=False)
    connection = os.environ.get('IOTHUB_DEVICE_CONNECTION_STRING', '').strip()
    if not connection:
        raise ValueError('Set IOTHUB_DEVICE_CONNECTION_STRING in .env or the environment')
    fields = dict(part.split('=', 1) for part in connection.split(';') if '=' in part)
    if fields.get('DeviceId') != device_id:
        raise ValueError('DeviceId in connection string must match --device-id')
    from azure.iot.device import Message
    from azure.iot.device.aio import IoTHubDeviceClient
    client = IoTHubDeviceClient.create_from_connection_string(connection, websockets=websockets)

    async def send(body):
        message = Message(body)
        message.content_type = 'application/json'
        message.content_encoding = 'utf-8'
        message.message_id = '{}:{}'.format(device_id, json.loads(body)['sequence_number'])
        await client.send_message(message)
    return client, send
