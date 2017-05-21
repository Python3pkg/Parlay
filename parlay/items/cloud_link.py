import parlay
from parlay.server.broker import Broker, run_in_thread
from twisted.web.client import Agent, readBody
from twisted.web.http_headers import Headers
import base64
from autobahn.twisted.websocket import WebSocketClientFactory, WebSocketServerProtocol, WebSocketClientProtocol, connectWS
from twisted.internet.protocol import ReconnectingClientFactory
import requests
import json
from base64 import b64encode, b64decode
from twisted.internet import ssl
import time
import logging
logger = logging.getLogger('parlay.items.cloud_link')

# make sure we have the secure version installed
try:
    import cryptography
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.backends import default_backend
except ImportError:
    print("Secure Version of parlay is required for cloud link")
    print("  To fix run: pip install parlay[secure]")


class CloudLinkSettings(object):
    """
    Class level settings to set for cloudLink
    """
    PUBLIC_KEY_LOCATION = None
    PRIVATE_KEY_LOCATION = None
    PRIVATE_KEY_PASSPHRASE = None  # can be none, but I recommend a passphrase anyway. It may or may not be more secure
    UUID_LOCATION = None # A file that contains My device UUID given by the cloud

    CLOUD_SERVER_ADDRESS = "https://pub1.parlay.cloud"


@parlay.local_item()
class CloudLink(parlay.ParlayCommandItem):

    connected_to_cloud = parlay.ParlayProperty(val_type=bool, default=False, read_only=True)
    base_link_uri = parlay.ParlayProperty(val_type=str, default=CloudLinkSettings.CLOUD_SERVER_ADDRESS+"/channels")
    device_registration_uri = parlay.ParlayProperty(val_type=str, default=CloudLinkSettings.CLOUD_SERVER_ADDRESS+"/device_stats/api/v1/device")
    uuid = parlay.ParlayProperty(val_type=str)

    def __init__(self):
        parlay.ParlayCommandItem.__init__(self, "parlay.items.cloud_link", "Cloud Link")
        self._http_agent = Agent(self._reactor)
        self.channel_uri = None
        self.cloud_factory = None

        if CloudLinkSettings.PRIVATE_KEY_LOCATION is None:
            raise RuntimeError("CloudLinkSettings.PRIVATE_KEY_LOCATION must be set for cloud to work")

        if CloudLinkSettings.UUID_LOCATION is None:
            raise RuntimeError("CloudLinkSettings.UUID_LOCATION must be set for cloud to work")

        try:
            with open(CloudLinkSettings.UUID_LOCATION, 'r') as uuid_file:
                self.uuid = uuid_file.read()

        except IOError:
            logger.warn("Error reading UUID file. Has this device been registered?")
            self.uuid = ""


    @parlay.parlay_command(async=True)
    def get_public_key(self):
        if CloudLinkSettings.PUBLIC_KEY_LOCATION is None:
            raise Exception("Public Key Location not Set. Please set CloudLinkSettings.PUBLIC_KEY_LOCATION")
        # simply open and read it
        with open(CloudLinkSettings.PUBLIC_KEY_LOCATION) as public_key_file:
            return public_key_file.read()

    @parlay.parlay_command(async=True)
    def generate_keys(self):
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )

        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(bytes(CloudLinkSettings.PRIVATE_KEY_PASSPHRASE))
        )
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        with open(CloudLinkSettings.PRIVATE_KEY_LOCATION, 'w') as key_file:
            key_file.write(str(private_pem))

        with open(CloudLinkSettings.PUBLIC_KEY_LOCATION, 'w') as key_file:
            key_file.write(str(public_pem))

    @parlay.parlay_command()
    def register_device_with_cloud(self, username, password, name, serial, group_id, notes):
        """
        Register the public and private keys with the cloud.
        If you already have them registered this will overwrite them
        :param username: username of a user with access to the cloud
        :param password: password of the user to authenticate with
        :param name: the name of the device
        :param serial:  the serial number of the device
        :param group_id:  the id given to the access group for this device
        :type group_id int
        :param notes: any notes to attach to this device
        :return: the UUID of the newly created device
        """
        result = requests.post(self.device_registration_uri, data={'name': name, 'serial': serial, 'group':group_id,
                                                          'notes': notes, "public_key": self.get_public_key()}, auth=(username, password))

        if result.ok:
            self.uuid = result.json()["UUID"]
            with open(CloudLinkSettings.UUID_LOCATION, 'w+') as uuid_file:
                uuid_file.write(self.uuid)

            return self.uuid
        else:
            raise RuntimeError("Error registering device: " + str(result.status_code) + " - " + result.text)

    @parlay.parlay_command(async=True)
    def http_request(self, path="/", encode='zlib'):
        """
        Do an http request on the broker
        :type path str
        """
        url = "http://localhost:" + str(Broker.get_instance().http_port)+path
        print(url)
        # http"://localhost:broker.http_port
        request = self._http_agent.request(
            'GET',
            url,
            Headers({'User-Agent': ['Twisted Web Client']}),
            None)
        request.addCallback(lambda response: readBody(response))
        request.addCallback(lambda html: (base64.b64encode(html.encode(encode))) if encode == "zlib" else html)
        return request

    @parlay.parlay_command(async=True)
    def connect_to_cloud_channel(self):
        """
        Connect up to the cloud channel with UUID uuid
        :param uuid: the UUID of this device. The uuid determines which channel you are connected to
        :return:
        """
        if self.uuid is None or self.uuid == "":
            raise RuntimeError("Must have a valid UUID to connect to the cloud")

        if self.connected_to_cloud:
            raise RuntimeError("Already Connected to cloud")

        yield self.get_channel()
        # cool, now set up the websocket connection to that channel
        reactor = self._adapter.reactor

        print("attempting to connect to", self.channel_uri)
        self.cloud_factory = CloudLinkWebsocketClientFactory(self, self.channel_uri,  reactor=reactor)
        self.cloud_factory.protocol = CloudLinkWebsocketClient
        self.cloud_factory.continueTrying = True  # attempt to reconnect

        if self.channel_uri.startswith("wss"):
            reactor.connectSSL(self.cloud_factory.host, self.cloud_factory.port, self.cloud_factory, ssl.ClientContextFactory())
        else:
            reactor.connectTCP(self.cloud_factory.host, self.cloud_factory.port, self.cloud_factory)

    @run_in_thread
    def get_channel(self):
        #first get a token we need to sign in order to prove we are who we say we are
        r = requests.get(str(self.base_link_uri) + "/get_device_token", params={"UUID": self.uuid, })

        token = r.json()["token"]
        # get the private Key
        with open(CloudLinkSettings.PRIVATE_KEY_LOCATION,'r') as key_file:
            private_key = serialization.load_pem_private_key(key_file.read(),
                                                             password=CloudLinkSettings.PRIVATE_KEY_PASSPHRASE,
                                                             backend=default_backend())

        # sign the token with our private key
        signer = private_key.signer(padding.PKCS1v15(), hashes.SHA256())
        signer.update(bytes(token))
        signature = signer.finalize()

        # get the randomly assigned channel for my UUID
        r = requests.get(str(self.base_link_uri) + "/get_device_group",
                         params={"UUID": self.uuid, "signature": b64encode(signature), "format": "PKCS1_v1_5"})
        if r.ok:
            self.channel_uri = r.json()["channel"]
        elif r.status_code == 400:
            raise Exception("UUID or Token not registered with Cloud.")
        elif r.status_code == 403:
            raise Exception("Signature didn't verify correctly. Bad private key or signature.")

    @parlay.parlay_command(async=True)
    def disconnect_from_cloud(self):
        self.cloud_factory.continueTrying = False  # stop retrying
        self.cloud_factory.disconnect()


class CloudLinkWebsocketClientFactory(WebSocketClientFactory, ReconnectingClientFactory):
    """
    Websocket Client library that keeps track of the cloud item for the CloudLinkWebsocketClient
    """
    def __init__(self, cloud_item, *args, **kwargs):
        WebSocketClientFactory.__init__(self, *args, **kwargs)
        self.cloud_item = cloud_item
        self.connected_protocol = None

    def disconnect(self):
        if self.connected_protocol is not None:
            self.connected_protocol.transport.loseConnection()

    def buildProtocol(self, addr):
        self.connected_protocol = WebSocketClientFactory.buildProtocol(self, addr)
        return self.connected_protocol


class CloudLinkWebsocketClient(WebSocketClientProtocol):
    """
    The websocket client that will bridge all messages between the broker and the cloud channel
    """

    def __init__(self, adapter=None):
        WebSocketClientProtocol.__init__(self)
        self._adapter = adapter if adapter is not None else Broker.get_instance().pyadapter
        # subscribe our cloud bridge method to be called on ALL messages
        #self._adapter.subscribe(self.send_message_to_cloud_channel)

    def send_message_to_cloud_channel(self, msg):
        """
        Send the message to the cloud. This should be subscribed to all messages
        :param msg:
        :return:
        """
        self.sendMessage(json.dumps(msg))

    def onConnect(self, response):
        print("Connected to cloud")
        self.factory.cloud_item.connected_to_cloud = True

    def connectionLost(self, reason):
        print("connection lost:", str(reason))
        self.factory.cloud_item.connected_to_cloud = False

    def onMessage(self, payload, isBinary):
        """
        When we get a message from the cloud link, publish it on our broker   PRIVATE_KEY_LOCATION
        `
        :param payload:
        :param isBinary:
        :return:
        """
        if isBinary:
            return  # Binary messages aren't supported

        try:
            msg = json.loads(payload)
            print(msg)

            # special logic for subscriptions. If we want to subscribe, then push it to the cloud
            if msg["TOPICS"].get('type') == 'subscribe':
                self._adapter.subscribe(self.send_message_to_cloud_channel, **(msg["CONTENTS"].get("TOPICS", {})))

            else:
                self._adapter.publish(msg, self.send_message_to_cloud_channel)
        except Exception as e:
            print("Exception on message" + str(payload) + "  " + str(e))


@parlay.local_item()
class CloudStressTest(parlay.ParlayCommandItem):

    data = parlay.ParlayDatastream(val_type=float, default=0.0)
    sleep_time = parlay.ParlayProperty(val_type=float, default=1)

    @parlay.parlay_command()
    def count_up(self, max):
        """

        :param max:
        :type max int
        :return:
        """
        for x in range(max):
            self.data = x
            time.sleep(self.sleep_time)

if __name__ == "__main__":
    # if run as main entry point, setup test key files
    CloudLinkSettings.PRIVATE_KEY_LOCATION = '/tmp/test_parlay_device'
    CloudLinkSettings.PUBLIC_KEY_LOCATION = '/tmp/test_parlay_device.pub.pem'
    CloudLinkSettings.PRIVATE_KEY_PASSPHRASE = "PASSWORD"
    CloudLinkSettings.UUID_LOCATION = "/tmp/test_parlay_uuid"
    c = CloudLink()
    d = CloudStressTest()
    parlay.start()