import pdb
from mininet.topo import Topo

class MyTopo( Topo ):

    def build(self):

        # spine switches
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')

        # leaf switches
        s3 = self.addSwitch('s3')
        s4= self.addSwitch('s4')
        
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')
        h3 = self.addHost('h3')
        h4 = self.addHost('h4')


        self.addLink(s3, s1)
        self.addLink(s3, s2)

        self.addLink(s4, s1)
        self.addLink(s4, s2)

        self.addLink(h1, s3)
        self.addLink(h2, s3)
        self.addLink(h3, s4)
        self.addLink(h4, s4)

topos = { 'mytopo' : (lambda: MyTopo()) }

