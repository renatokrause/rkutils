apt install xrdp
sed -i 's/port=vsock/port=tcp/' /etc/xrdp/xrdp.ini
sed -i 's/-1:3389/:3389/' /etc/xrdp/xrdp.ini
echo "sleep 2s" >> /etc/xrd/reconnectwm.sh
echo "setxkbmap -model abnt2 -layout br" >> /etc/xrd/reconnectwm.sh
