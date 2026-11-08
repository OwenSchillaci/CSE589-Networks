import pdb
from mininet.topo import Topo

class MyTopo( Topo ):

    def __init__(self):

        Topo.__init__(self)
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')
        SwitchList=(s1, s2)


        self.addLink(h1, s1)
        self.addLink(h1, s2)
        self.addLink(h2, s1)
        self.addLink(h2, s2)
topos = { 'mytopo' : (lambda: MyTopo()) }

