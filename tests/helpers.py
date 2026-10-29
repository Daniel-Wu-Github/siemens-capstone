"""Small synthetic rows shared by the test tables."""


def transfer(source, dest, material='P-1', bin_='B-1', quantity=10):
    """One replenishment-log row with just the fields the cleaners read."""
    return {'Transfer Order Number': '1', 'Transfer order item': '1', 'Material': material,
            'Source Storage Type': source, 'Dest. Storage Type': dest,
            'Dest.Storage Bin': bin_, 'Dest.target quantity': quantity,
            'Confirmation Date': '2026-05-04'}


def bin_row(material, bin_, available, storage_type='405', total=None, unit='PC'):
    return {'Material': material, 'Material Description': f'{material} description',
            'Storage Type': storage_type, 'Storage Bin': bin_,
            'Total Stock': total if total is not None else available,
            'Available stock': available, 'Base Unit of Measure': unit,
            'Storage Unit': '', 'Special Stock Number': ''}


def tolerance_row(part, location, minimum, maximum, type_=''):
    return {'Part': part, 'Location': location, 'Type': type_, 'Min': minimum, 'Max': maximum}
