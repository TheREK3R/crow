import base64
import socket 
import aes_crypt
import rsa_encrypt
import settings
import hashlib
import sqlite3
from Crypto import Random
import time

#DB String Diagram 

#DB
#share||share||share|||DB|||DB
#val1|val2|val3||share||share


#defines a host to send multicast messages to, based on the settings.py file
class Host():
    def __init__(self):
        self.host = settings.MULT_ADDR
        self.port = settings.MULT_PORT


#Delets all entries in all shares databases for a given ID
def delete_all(id):
    
    #For each db in settigs
    for i in settings.DBS:

        #connect to that database, remove all shares with id == provided_id and commit the action
        conn = sqlite3.connect( settings.DBdir + i+".db")
        conn.cursor().execute("DELETE FROM enc_shares WHERE id = ?", [id])
        conn.commit()
        conn.close()
    return


#Fills each database on this authentication node with the updates provided by the updater
def fill_dbs(updates):

    #for each database to be updated
    for i in updates:
        
        #Set up database connection
        conn = sqlite3.connect(settings.DBdir + i+".db")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        #grab the shares within the current database update set
        shares = updates[i]

        #If the database is the secrets database
        if i == 'secrets':

            #make sure that the proper table exists
            c.execute("CREATE TABLE IF NOT EXISTS secrets(id PRIMARY KEY, name, secret, timestamp DOUBLE)")
            
            #for each share
            for j in shares:

                #convert str to list
                share = j.split("|")

                #if the update is a deletion than delete all associated database entries.
                # The secrets update will be the last in the list of databases
                if(share[2] == "DEL"):
                    delete_all(share[0])
                
                #insert or replace the share into the secrets table (REPLACE is better represented as INSERT OR REPLACE)
                c.execute("REPLACE INTO secrets VALUES (?, ?, ?, ?)", share)
        
        #For all non-secrets database
        else:
            
            #skip if db has no associated shares
            if shares == [''] or shares == None:
                conn.close()
                continue

            #make sure that the proper table exists
            c.execute("CREATE TABLE IF NOT EXISTS enc_shares(id PRIMARY KEY, share, timestamp DOUBLE)")
            
            #For each share in this update set
            for j in shares:

                #Convert str to list
                share = j.split("|")

                #Insert or replace the share into the table
                c.execute("REPLACE INTO enc_shares VALUES(?, ?, ?)", share)
        
        #commit changes
        conn.commit()
        conn.close()
    return


#Grabs all timestamps from the secrets database.
def grab_timestamps():

    #connect to secrets db
    conn = sqlite3.connect(settings.DBdir + "secrets.db") 
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    #make sure secrets table exists
    c.execute("CREATE TABLE IF NOT EXISTS secrets(id PRIMARY KEY, name, secret, timestamp DOUBLE)")
    
    #Grab timestamp
    c.execute("SELECT timestamp FROM secrets")
    timestamps = c.fetchall()

    #If timestamp should be zero change it to zero
    if timestamps == None:
        timestamps = "0"

    #concatenate all timestamps
    payload = ""
    for i in timestamps:
        payload = payload + str(i['timestamp']) + "|"
    payload = payload[:-1]

    #Close the connection and return the timestamps
    conn.close()
    return payload


#This challenges the authentication servers, it questions them to pick one to communicate with on wakeup
def challenge(my_number):

    #Creates a host object for use in multicast socket
    host = Host()

    #Creates a multicast socket to communicate with all auth nodes at once
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)
        
        #Creates a socket to recieve a unique number from the first auth node to respond to the contest
        data = ""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as us:
            #sets a small timeout in case this is the first auth node in the system or the response is delayed
            us.settimeout(1)
            us.bind(('0.0.0.0', 44443))

            #Sends a messafe to the other auth nodes to start a contest
            s.sendto(aes_crypt.aes_enc(rsa_encrypt.get_pub_key_auth(), "regA:"+str(my_number)), ((host.host, host.port)))

            #Recieves encrypted number from auth node
            data, address = us.recvfrom(4096)

        #Decrypt the recieved number using auth private key
        data = aes_crypt.aes_dec(rsa_encrypt.get_priv_key_auth(), data)
        
        #Check to make sure the message wasnt tampered with, return error if it was
        if data == -1 or data == -2:
            return 1
        
        #convert the number to a string 
        data = str(data, 'ascii')

        #encrypt the number with the auth public key and send it back to the auth nodes, letting them know which one was chosen and what action to preform
        #in this case the asction is provising updates to the auth server
        s.sendto(aes_crypt.aes_enc(rsa_encrypt.get_pub_key_auth(), "you!:" + data + ":" + "woke"), ((host.host, host.port)))
        return address


#This function handles the updating of an auth node by 
#downloading the shares and secrets from another auth node
def updateee(my_number):

    #open socket to recieve shares into
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('0.0.0.0', 44441))
        s.listen(5)
        address = 0
        
        #Attempt to update the node
        try:    
            #make sure that challenge exits succesfully and grab host address
            address = challenge(my_number)
            while address == 1:
                address  = challenge(my_number)
        
        #If socket times out then return, this is likely the first auth node to be activated
        except socket.timeout:
            print("No Auth Nodes Found")
            return

        #accept connection from the node that shares will be pulled from
        cli, addr = s.accept()
        while not addr[0] == address[0]:
            cli, addr = s.accept()
        
        #Challenge response authentication, the node recieves a number from the auth node responsible for the update
        #and sends the number + 1 to the other node
            
        data = cli.recv(1024)

        #decrypt number and increment it by one
        data = aes_crypt.aes_dec(rsa_encrypt.get_priv_key_auth(), data)
        data = str(data, 'ascii')
        data = str(int(data) + 1)

        #encrypt number and return it to the host
        #send the timestamp of the most recent share along with the number
        cli.send(aes_crypt.aes_enc(rsa_encrypt.get_pub_key_auth(), data + ":" + grab_timestamps()))
        
        #Recv data until the sender is done
        data = b""

        try:
            while 1==1:
                temp = cli.recv(4096)
                if temp:
                    data += temp
                else:
                    print("end of recv")
                    break
        
        #if the sender loses the connection then quit
        except:
            print("registered: 0 updates")
            return

        #Decrypt the data with the auth private key
        data = aes_crypt.aes_dec(rsa_encrypt.get_priv_key_auth(), data)

        #if the data is invalid return error
        if data == -2 or data == -1:
            return -1

        #if no databases hold data then return
        if data == b':::'*settings.TOTAL:
            print("registered: 0 updates")
            return

        #split the data into a list of databases
        data = str(data, 'ascii').split(":::")

        #for each database split the entries into a list
        for i in range(len(data)):
            data[i] = data[i].split("::")
        
        #store the list of db's into a disctonary
        #they are sent in the order they are listed in settings.py
        updates = {}
        for i in range(len(settings.DBS)):
            updates[settings.DBS[i]] = data[i]
        
        #store the secrets database into the dictionary
        updates['secrets'] = data[-1]
        
        #fill the databases
        fill_dbs(updates)

        #exit, ptinting the number of shares that were updated
        print("registered: " + str(len(updates['secrets'])) + " updates")
    return

#Handles the sending of shares to another auth server asking for registration 
#takes the address of the node to update
def updater(address):

    #Open a socket to send the shares from, connecting to the provided address
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((address, 44441))

        #create a random number for the challenge response authentication
        challenge = int.from_bytes(Random.get_random_bytes(10), 'big')

        #Send the number to the recieving node
        s.send(aes_crypt.aes_enc(rsa_encrypt.get_pub_key_auth(), str(challenge)))
        
        #Get the number back, along with the updatee's timestamps
        #Recieve until done
        response = b""
        try:
            while 1==1:
                temp = s.recv(4096)
                if temp:
                    response += temp
                    print("1")
                else:
                    print("end of recv")
                    break
        except:
            print("exception??")
            return -1

        #decrypt response
        response = aes_crypt.aes_dec(rsa_encrypt.get_priv_key_auth(), response)
        
        #return error if data is corrupted
        if response == -1 or response == -2:
            return -1
        
        #split response into timestamp and the response number
        response = str(response, 'ascii').split(":")

        #confirm that the response is correct
        if (challenge + 1) == int(response[0], 0):
           
            #grab timestamp
            timestamps = response[1].split("|")
            print(timestamps)
            #create holder for share information
            data = ""

            #for each database
            for i in settings.DBS:
                
                #set up connection
                conn = sqlite3.connect(settings.DBdir + i + ".db")
                conn.row_factory = sqlite3.Row
                c = conn.cursor()

                #Make sure table exists
                c.execute("CREATE TABLE IF NOT EXISTS enc_shares(id PRIMARY KEY, share, timestamp DOUBLE)")

                #Grab all shares from the current database with timestamp greater than the client's timestamp
                c.execute("SELECT * FROM enc_shares")
                d = c.fetchall()

                shares = []
                #for each share
                for i in range(len(d)):
                    #Join the components into a string if they are needed by the updatee
                    if not str(d[i]['timestamp']) in timestamps:
                        shares.append(d[i]["id"] + "|" + d[i]["share"] + "|" + str(d[i]["timestamp"]))
                
                #Join each share together
                shares = "::".join(shares)

                #add the information from this database to the database string
                data += (shares + ":::")

                #close connection
                conn.close()

            #open the secrets db
            conn = sqlite3.connect(settings.DBdir + "secrets.db")
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            #make sure table exists
            c.execute("CREATE TABLE IF NOT EXISTS secrets(id PRIMARY KEY, name, secret, timestamp DOUBLE)")

            #Grab all shares past the client timestamp 
            c.execute("SELECT * FROM secrets")
            d = c.fetchall()

            secrets = []
            #For each secret
            for i in range(len(d)):
                
                #convert the share to a string if it is needed by the updatee
                if not str(d[i]['timestamp']) in timestamps: 
                    secrets.append(d[i]["id"] + "|" + d[i]["name"] + "|" + d[i]["secret"] + "|" + str(d[i]['timestamp']))
            
            #join all the shares and add them to the db string
            data += "::".join(secrets)

            #send the databases to the client and exit
            s.send(aes_crypt.aes_enc(rsa_encrypt.get_pub_key_auth(), data))
    return 0