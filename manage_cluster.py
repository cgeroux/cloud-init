#!/usr/bin/env python
from __future__ import print_function
import optparse as op
import xml.etree.ElementTree as xml
import time
from os import environ as env
import novaclient.v1_1.client as nvclient
import novaclient
import sys

#maximum amount of time to wait for node to boot before skip rest of setup
maxWaitTimeForNodeBoot=20

class Node(object):
  """Class for working with compute nodes in OpenStack using the Python API in 
  a nicer simpler way that hides much of the complexity.
  
  It however imposes some amount of extra structure not present in OpenStack
  such as unique node names and may not expose all the functionality present
  in the lower level OpenStack python API.
  """
  
  #structures to describe the structure of the xml element describing the node
  requiredSettings=["name","image","flavor","key-name","network"]
  requiredSettingsAttributes={}
  optionalSetings=["floating-ip","cloud-init"]
  optionalSetingsAttributes={"cloud-init":["match","replace"]}
  
  def __init__(self,nodeElement,nova):
    """Parse the XML element nodeElement to initialize the settings for the node
    """
    self.nova=nova
    self.settings={}#a dictionary containing the text of xml elements with the  
      #name of the xml element as the key
    self.settingsAttributes={}#A 2D dictionary with xml element name and 
      #attribute as the two keys to access the element attribute value
    
    #parse required settings
    #if they are not present raise an exception
    for requiredSetting in self.requiredSettings:
      
      #get all elements of this name
      requiredSettingElements=nodeElement.findall(requiredSetting)
      
      #check that we have one and only one element for this requiredSetting
      if len(requiredSettingElements)!=1:
        raise Exception("Must have one and only one \""+requiredSetting
          +"\" elements per node element")
      
      #check to make sure there is text in the element
      setting=requiredSettingElements[0].text
      if setting==None:
        raise Exception("\""+requiredSetting+"\" is empty, must have a value")
      
      #save the setting
      self.settings[requiredSetting]=setting
      
      #Get attributes of element if it has any
      if(requiredSetting in self.requiredSettingsAttributes.keys()):
        
        self.settingsAttributes[requiredSetting]={}
        for attribute in self.requiredSettingsAttributes[requiredSetting]:
          self.settingsAttributes[requiredSetting][attribute] \
            =optionalSettingElements[0].get(attribute)

    #parse optional settings
    for optionalSetting in self.optionalSetings:
      
      #get all elements of this name
      optionalSettingElements=nodeElement.findall(optionalSetting)
      
      #check that one or less elements
      numElements=len(optionalSettingElements)
      if numElements>1:
        raise Exception("Must have one or fewer \""+optionalSetting
          +"\" elements per node element")
      
      #If there is an element get the setting
      if numElements==1:
        setting=optionalSettingElements[0].text
        if setting==None:
          raise Exception("\""+requiredSetting+"\" is empty, must have a value")
        
        #save the setting
        self.settings[optionalSetting]=setting
        
        #Get attributes of element if it has any
        if(optionalSetting in self.optionalSetingsAttributes.keys()):
          
          self.settingsAttributes[optionalSetting]={}
          for attribute in self.optionalSetingsAttributes[optionalSetting]:
            self.settingsAttributes[optionalSetting][attribute] \
              =optionalSettingElements[0].get(attribute)
          
      else:#no node, set setting to None
        self.settings[optionalSetting]=None
  def _assignFloatingIP(self):
    """Assigns a floating ip if not already assigned to a node and it is 
    available for use
    """
    
    #check that the ip exists
    ipList=self.nova.floating_ips.list()
    requestedIPExists=False
    ipToUse=None
    for ip in ipList:
      
      #if we found the ip we wanted to assign in the list
      if ip.ip==self.settings["floating-ip"]:
        requestedIPExists=True
        ipToUse=ip
    
    #if ip doesn't exist, don't add it
    if not requestedIPExists:
      print("    WARNING: The requested floating ip "
        +self.settings["floating-ip"]
        +"\" does not exist. Not assigning it to node.")
      return
    
    #If ip is already assigned to a node don't reassign it
    if(ipToUse.instance_id!=None):
      server=self.nova.servers.find(id=ipToUse.instance_id)
      print("    WARNING: floating ip is already assigned to the \""
        +server.name+"\" node; not reassigning it.")
      return
    
    #assign the ip to the node
    print("    Adding floating ip "+self.settings["floating-ip"]+" ...")
    self.instance.add_floating_ip(ipToUse)
  def _createUserDataFile(self):
    """Returns a file object pointing to the user data file
    """
    
    #if there isn't a cloud init file given nothing to do
    if(self.settings["cloud-init"]==None):
      return None
      
    #TODO: need to do a string replace based on values of 
    #self.settingsAttributes["cloud-init"]["match"] and
    #self.settingsAttributes["cloud-init"]["replace"]
    
    return open(self.settings["cloud-init"],'r')
  def _createNewNode(self):
    """Boots a new node
    """
    
    print("    Creating new node ",end="")
    
    #Get parameters for creating a node
    image=self.nova.images.find(name=self.settings["image"])
    flavor=self.nova.flavors.find(name=self.settings["flavor"])
    net=self.nova.networks.find(label=self.settings["network"])
    nics=[{'net-id':net.id}]
    
    userDataFile=self._createUserDataFile()
    
    if userDataFile==None:
      self.instance=self.nova.servers.create(
        name=self.settings["name"]
        ,image=image
        ,flavor=flavor
        ,key_name=self.settings["key-name"]
        ,nics=nics)
    else:
      self.instance=self.nova.servers.create(
        name=self.settings["name"]
        ,image=image
        ,flavor=flavor
        ,key_name=self.settings["key-name"]
        ,nics=nics
        ,userdata=userDataFile)
    
    #wait for it to spin up before trying to assign an ip
    iters=0
    server=self.nova.servers.find(name=self.settings["name"])
    while (server.status!="ACTIVE" and iters<maxWaitTimeForNodeBoot):
      print(".",end="")
      sys.stdout.flush()
      time.sleep(1)
      server=self.nova.servers.find(name=self.settings["name"])
      iters+=1
    
    #if node still not booted, skip any more setup
    if iters>=maxWaitTimeForNodeBoot:
      print("      WARNING: node took too long to boot setup may be "
        +"incomplete.")
      return
    
    #at this point the node should be booted
    print()#add a new line
    print("      node booted")
    self.instance=server#assign updated version of server
      
    #if we have a floating ip add it
    if self.settings["floating-ip"]!=None:
      self._assignFloatingIP()
  def create(self):
    """Creates a node if needed, and ensures node is active
    """
    
    print("  booting the node \""+self.settings["name"]+"\" ...")
    
    try:
      
      #try getting an existing node, if it fails there may be no node
      #or multiple nodes
      existingNode=self.nova.servers.find(name=self.settings["name"])
      
      #Check if the node is active
      #TODO: should handle more statuses correctly
      if(existingNode.status=="ACTIVE"):
        print("    Node is already active, nothing to be done")
      else:
        print("    WARNING: Node in an unknown state \""+existingNode.status
          +"\"; script doing nothing for this node; manual intervention may "
          +"be required")
    
    #no node with that name
    except novaclient.exceptions.NotFound:
      
      #no existing node found, create a new one
      self._createNewNode()
      
    #more than one match for that name
    except novaclient.exceptions.NoUniqueMatch:
      raise Exception("Multiple nodes found with a matching "
      +"name unable to determine which node is to be booted!")
  def delete(self):
    """deletes the node
    """
    
    print("  deleting the node \""+self.settings["name"]+"\" ...")
    
    try:
      server=self.nova.servers.find(name=self.settings["name"])
      self.nova.servers.delete(server)
      print("    Node deleted")
    except novaclient.exceptions.NotFound:
      print("    No node found with given name. Doing nothing")
class Cluster(object):
  """Container for managing a number of VM nodes
  """
  
  def __init__(self,clusterElement,nova):

    #loop over all node nodes
    nodeElements=clusterElement.findall("node")
    if len(nodeElements)==0:
      raise Exception("No \"node\" elements found under \"cluster\" element! "
        +"can not create an empty cluster.")
    
    #Initialize nodes
    self.nodes=[]
    for nodeElement in nodeElements:
      
      #get number of instances to make of the node
      numInstances=nodeElement.get("num-instances")
      if numInstances!=None:
        numInstances=int(numInstances)
      
      if numInstances==None or numInstances==1:
        
        #create one instance
        self.nodes.append(Node(nodeElement,nova))
      else:
        
        #create numInstances with a suffix added to the name
        for i in range(numInstances):
          node=Node(nodeElement,nova)
          node.settings["name"]+="-"+str(i)
          self.nodes.append(node)
  def create(self):
    """Creates all the nodes in the cluster as needed and ensures they are all
    active
    """
    
    for node in self.nodes:
      node.create()
  def delete(self):
    """Deletes all the nodes in the cluster
    """
    
    for node in self.nodes:
      node.delete()
def addParserOptions(parser):
  """Adds command line options
  """
  
  pass
def parseOptions(actions):
  """Parses command line options
  
  """
  
  parser=op.OptionParser(usage="Usage: %prog [options] CONFIG.xml ACTION"
    ,version="%prog 1.0",description="Performs ACTION on the OpenStack "
    +"cluster described by CONFIG.xml. ACTION can be one of "+str(actions))
  
  #add options
  addParserOptions(parser)
  
  #parse command line options
  return parser.parse_args()
def main():
  
  #these actions should match methods in the Cluster class
  actions=["create","delete"]
  
  #these verbs are just for messages and there should be one for each 
  #corresponding action
  verbs=["creating","deleting"]
  
  #parse command line options
  (options,args)=parseOptions(actions)
  
  #check we got the expected number of arguments
  if (len(args)!=2):
    raise Exception("Expected two arguments, the xml configuration file "
    +"describing the cluster followed by an action.")
  
  #check we got an action we recognize
  if args[1] not in actions:
    raise Exception(args[1]+" not in known actions "+str(actions))
  
  print(verbs[actions.index(args[1])]+" the cluster described by \""
    +args[0]+"\" ...")
  
  #get root element of config file
  tree=xml.parse(args[0])
  root=tree.getroot()
  
  #create the nova client
  nova=nvclient.Client(auth_url=env['OS_AUTH_URL']
    ,username=env['OS_USERNAME'],api_key=env['OS_PASSWORD']
    ,project_id=env['OS_TENANT_NAME'],region_name=env['OS_REGION_NAME'])
  
  #Initialize the cluster
  cluster=Cluster(root,nova)
  
  #perform the given action on the cluster
  clusterAction=getattr(cluster,args[1])
  clusterAction()
  
  """
    print "Settings for salt_master:"
    print "  saltMasterImage=",options.saltMasterImage
    print "  saltMasterCloudInit=",options.saltMasterCloudInit
    print "  saltMasterFlavor=",options.saltMasterFlavor
    print "  saltMasterKeyName=",options.saltMasterKeyName
    print "  saltMasterFloatingIP=",options.saltMasterFloatingIP
    
    #nova boot --image options.saltMasterImage 
    #--user-data options.saltMasterCloudInit --flavor options.saltMasterFlavor 
    #--key-name options.saltMasterKeyName salt_master
    cmd=["nova","boot"
      ,"--image",options.saltMasterImage
      ,"--user-data",options.saltMasterCloudInit
      ,"--flavor",options.saltMasterFlavor
      ,"--key-name",options.saltMasterKeyName
      ,"--key-name",options.saltMasterKeyName
      ,"salt_master"
      ]
    process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    stdout,stderr=process.communicate()
    print stdout
    print stderr
    
    #now wait some time and then assign a floating-ip
    time.sleep(5)
    cmd=["nova","add-floating-ip","salt_master",options.saltMasterFloatingIP]
    process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    stdout,stderr=process.communicate()
    print stdout
    print stderr
    
    #create hadoop-master (get local salt_master ip)
  """
if __name__ == "__main__":
  main()