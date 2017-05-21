from twisted.internet.protocol import DatagramProtocol
from twisted.internet import reactor, task
import json
import socket


UDP_MULTICAST_GROUP = "232.0.10.5"  # The UDP mluticast group that we're  broadcasting to
UDP_MULTICAST_PORT = 8085  # The port on that group
CONSUMER_RESPONSE_PORT = 9001  # The port on the client to respond to


class ParlayAdvertiser(DatagramProtocol):
    """
    This protocol will advertise that a Parlay system is on this machine
    """

    def startProtocol(self):
        """
        Called after protocol has started listening.
        """
        # Set the TTL>1 so multicast will cross router hops:
        self.transport.setTTL(5)
        # Join a specific multicast group:
        self.transport.joinGroup(UDP_MULTICAST_GROUP)

    def datagramReceived(self, datagram, address):
        print("Datagram %s received from %s" % (repr(datagram), repr(address)))
        try:
            request = json.loads(datagram)
            if request.get("type", None) == "GET_PARLAY_INFO":
                info = {'type': "PARLAY_INFO", 'info': {'name': socket.gethostname()}}
                self.transport.write(json.dumps(info), (address[0], CONSUMER_RESPONSE_PORT))

        except ValueError:
            pass  # Not valid JSON


class ParlayConsumer(DatagramProtocol):
    """
    This protocol will request for advertisements to be sent
    """

    def startProtocol(self):
        # Join the multicast address, so we can receive replies:
        self.transport.joinGroup(UDP_MULTICAST_GROUP)

        self._doRequest()
        self.found_hosts = {}

    def _doRequest(self):
        request = {"type": "GET_PARLAY_INFO"}
        self.transport.write(json.dumps(request), (UDP_MULTICAST_GROUP, UDP_MULTICAST_PORT))

    def print_output(self):
        print("Active Parlay Hosts")
        if len(self.found_hosts) == 0:
            print("None")
            return

        # get them in a table
        table = [["name", "URL"]]
        table.extend([ (v, k) for k,v in self.found_hosts.items()])
        # print the table
        row_format = "{:<35}" * (len(table[0]))
        for row in table:
            print(row_format.format(*row))

    def datagramReceived(self, datagram, address):
        try:
            info = json.loads(datagram)
            if info.get("type", None) == "PARLAY_INFO":
                url="http://"+address[0]+":"+str(8080)
                self.found_hosts[url] = info.get("info", {}).get("name", "N/A")

        except ValueError:
            pass  # Not valid JSON


def main():
    """
    Function to run if this is the entry point and not imported like a module
    :return:
    """
    consumer = ParlayConsumer()
    reactor.listenMulticast(CONSUMER_RESPONSE_PORT, consumer, listenMultiple=True)

    def print_and_exit():
        consumer.print_output()
        reactor.stop()

    reactor.callLater(1.5, print_and_exit)
    reactor.run()

if __name__ == "__main__":
    main()
