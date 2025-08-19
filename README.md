# RPA - Fritagelse for leverandørmodregning

This robot is used to allow employees without access to SAP to order automated changed to "Leverandørmodregning".
The robot is activated using an OS2Forms formula which delivers an email.

## Arguments

The robot expects the following arguments:

```json
{
    "approved_senders": ["az12345", "az98765"]
}
```

__approved_senders__: A whitelist of people who are allowed to activate the robot.
