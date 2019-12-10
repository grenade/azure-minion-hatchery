import secrets
import string
import uuid
import yaml
from azure.common.client_factory import get_client_from_cli_profile
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient

resourceClient = get_client_from_cli_profile(ResourceManagementClient)
computeClient = get_client_from_cli_profile(ComputeManagementClient)
networkClient = get_client_from_cli_profile(NetworkManagementClient)


def spawnHive (locationNames, resourceGroupNames, virtualNetworkNames, subnetNames, firewallIpWhitelist = ['Internet']):
  locationIndex = 0
  resourceGroupIndex = 0
  virtualNetworkIndex = 0
  subnetIndex = 0
  for locationName in locationNames:
    for resourceGroupSuffix in resourceGroupNames:
      resourceGroupName = '{}-{}'.format(locationName, resourceGroupSuffix)
      resourceGroup = resourceClient.resource_groups.create_or_update(
        resourceGroupName,
        {
          'location': locationName
        }
      )
      print('info: updated resource group: {}'.format(resourceGroup.name))
      for virtualNetworkName in virtualNetworkNames:
        virtualNetworkAddressPrefix = '10.{}.{}.0/24'.format(locationIndex, (((resourceGroupIndex + 1) * len(resourceGroupNames)) + virtualNetworkIndex))
        virtualNetworkCreateOrUpdate = networkClient.virtual_networks.create_or_update(
          resourceGroupName,
          virtualNetworkName,
          {
            'location': locationName,
            'address_space': {
              'address_prefixes': [
                virtualNetworkAddressPrefix
              ]
            }
          }
        )
        virtualNetworkCreateOrUpdate.wait()
        print('info: updated virtual network: {} / {}, with address prefix: {}'.format(resourceGroupName, virtualNetworkName, virtualNetworkAddressPrefix))
        for subnetName in subnetNames:
          subnetAddressPrefix = '10.{}.{}.0/24'.format(locationIndex, (((resourceGroupIndex + 1) * len(resourceGroupNames)) + virtualNetworkIndex))
          subnetCreateOrUpdate = networkClient.subnets.create_or_update(
            resourceGroupName,
            virtualNetworkName,
            subnetName,
            {
              'address_prefix': subnetAddressPrefix
            }
          )
          subnet = subnetCreateOrUpdate.result()
          print('info: updated subnet: {} / {} / {}, with address prefix: {}'.format(resourceGroupName, virtualNetworkName, subnet.name, subnetAddressPrefix))

          networkSecurityGroupName = 'nsg-{}-default'.format(resourceGroupName)
          networkSecurityGroupCreateOrUpdate = networkClient.network_security_groups.create_or_update(
            resourceGroupName,
            networkSecurityGroupName,
            {
              'location': locationName,
              'security_rules': [
                {
                  'name': 'rdp-only',
                  'description': 'allow: inbound tcp connections, for: rdp, from: firewallIpWhitelist, to: any host, on port: 3389',
                  'access': 'Allow',
                  'protocol': 'Tcp',
                  'direction': 'Inbound',
                  'priority': 110,
                  'source_address_prefixes': firewallIpWhitelist,
                  'source_port_range':'*',
                  'destination_address_prefix': '*',
                  'destination_port_range': '3389'
                },
                {
                  'name': 'ssh-only',
                  'description': 'allow: inbound tcp connections, for: ssh, from: firewallIpWhitelist, to: any host, on port: 22',
                  'access': 'Allow',
                  'protocol': 'Tcp',
                  'direction': 'Inbound',
                  'priority': 120,
                  'source_address_prefixes': firewallIpWhitelist,
                  'source_port_range':'*',
                  'destination_address_prefix': '*',
                  'destination_port_range': '22'
                }
              ]
            }
          )
          networkSecurityGroup = networkSecurityGroupCreateOrUpdate.result()
          print('info: updated network security group: {}, with {} security rules: {}'.format(networkSecurityGroup.name, len(networkSecurityGroup.security_rules), str.join(', ', map(lambda rule : rule.name, networkSecurityGroup.security_rules))))
          subnetIndex += 1
        virtualNetworkIndex += 1
      resourceGroupIndex += 1
    locationIndex += 1


def spawnMinion (instanceId, locationName, resourceGroupName, imageId, machine, virtualNetworkName = 'default', subnetName = 'default'):
  virtualMachineName = 'vm-{}'.format(instanceId)
  print('spawning minion: {} in resource group: {}'.format(virtualMachineName, resourceGroupName))

  publicIpAddressName =  'pia-{}'.format(instanceId)
  publicIpAddress = networkClient.public_ip_addresses.create_or_update(resourceGroupName, publicIpAddressName, {
    'location': locationName,
    'public_ip_allocation_method': 'Dynamic'
  }).result()
  print('spawned public ip address: {} in resource group: {}'.format(publicIpAddress.name, resourceGroupName))
  
  subnet = networkClient.subnets.get(resourceGroupName, virtualNetworkName, subnetName)
  print('found subnet; id: {}, name: {}'.format(subnet.id, subnet.name))
  
  networkSecurityGroup = networkClient.network_security_groups.get(resourceGroupName, 'nsg-{}-default'.format(resourceGroupName))
  print('found network security group; id: {}, name: {}'.format(networkSecurityGroup.id, networkSecurityGroup.name))

  networkInterfaceName = 'ni-{}'.format(instanceId)
  networkInterface = networkClient.network_interfaces.create_or_update(resourceGroupName, networkInterfaceName, {
    'location': locationName,
    'ip_configurations': [{
      'name': 'ic-{}'.format(instanceId),
      'private_ip_allocation_method': 'Dynamic',
      'subnet': { 'id': subnet.id },
      'public_ip_address': { 'id': publicIpAddress.id }
    }],
    'network_security_group': { 'id': networkSecurityGroup.id }
  }).result()
  print('spawned network interface: {}, associated with public ip: {}, on subnet: {}, in network security group: {}'.format(networkInterface.name, publicIpAddress.name, subnet.name, networkSecurityGroup.name))

  virtualMachineCreateOrUpdate = computeClient.virtual_machines.create_or_update(
    resourceGroupName,
    virtualMachineName,
    {
      'location': locationName,
      'os_profile': {
        'computer_name': virtualMachineName,
        'admin_username': 'Administrator',
        'admin_password': ''.join(secrets.choice(string.ascii_letters + string.digits) for i in range(12))
      },
      'hardware_profile': {
        'vm_size': machine
      },
      'storage_profile': {
        'image_reference': {
          'id' : imageId
        },
      },
      'network_profile': {
        'network_interfaces': [{
          'id': networkInterface.id,
        }]
      },
    })
  virtualMachineCreateOrUpdate.wait()



with open('hive.yaml', 'r') as stream:
  hive = yaml.safe_load(stream)
  for minion in hive['minion']:
    spawnHive(
      locationNames = [minion['location']],
      resourceGroupNames = [minion['resource-group']],
      virtualNetworkNames = ['default'],
      subnetNames = ['default'],
      firewallIpWhitelist = ['185.189.196.216']
    )
    spawnMinion(
      instanceId = str(uuid.uuid4())[-12:],
      locationName = minion['location'],
      resourceGroupName = minion['resource-group'],
      imageId = minion['image'],
      machine = minion['machine'])